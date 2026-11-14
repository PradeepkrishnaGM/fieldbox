"""Competitive co-evolution of doctrine genomes (PLAN.md 4.5, DECISIONS.md "M8 design").

Every game is a pure function of (blue spec, red spec, world preset, map seed,
game seed), so a run is reproducible whatever the worker count. All
randomness comes from `make_rng(run_seed, <stream>, generation)`, so a run
can resume from the last finished generation file alone.

Layout of `runs/evo/<run>/`:
    meta.json           preset, seed, parameters
    gen_XXX.json        one per finished generation (population, fitness, champion, Elo, ...)
    replays/gen_XXX.fbr champion vs previous champion on map 0
    xgen.json           cross-generation win-rate matrix (end of run)
    status.json         progress for the UI (not part of the reproducible output)
"""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import time
from collections import Counter
from dataclasses import asdict, replace
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .. import params as P
from ..agents import Commander, DoctrineGenome, RushAgent, TacticalAgent
from ..core.rng import derive_seed, make_rng
from ..sim.units import Side
from ..world import generate_world
from .arena import run_game
from .stats import mean_std, performance_rating, trait_stats, winrate_matrix

MAP_MOD = 1_000_000_007
SEED_MOD = 2 ** 31
DEFAULT_ROOT = "runs/evo"


# -- games ------------------------------------------------------------------

def make_agent(spec: str):
    """`anchor:<name>` or `genome:<json>` -> a fresh agent."""
    kind, _, body = spec.partition(":")
    if kind == "genome":
        return Commander(DoctrineGenome.from_json(body))
    if body == "doctrine-default":
        return Commander()
    if body == "rush":
        return RushAgent()
    if body == "doctrine-legacy":
        return TacticalAgent()
    raise ValueError(f"unknown agent spec '{spec}'")


@lru_cache(maxsize=64)
def _world(preset: str, seed: int):
    return generate_world(seed, preset)


def run_job(job: Tuple[str, str, str, int, int]) -> Tuple[int, float, float, int, str]:
    """One game. Returns (winner: 0 BLUE / 1 RED / -1 draw, vp_blue, vp_red, turns, cause)."""
    blue, red, preset, map_seed, game_seed = job
    g = run_game(make_agent(blue), make_agent(red), _world(preset, map_seed), seed=game_seed)
    w = g.winner()
    return (-1 if w is None else int(w), g.vp[Side.BLUE], g.vp[Side.RED], g.turn, g.result[1])


def _score(res, blue: bool, p: P.EvoParams, with_vp: bool = True) -> float:
    """Result of a game from one side's view: win 1 / draw .5 / loss 0, plus a small VP-margin bonus."""
    w, vb, vr = res[0], res[1], res[2]
    mine = 0 if blue else 1
    base = 0.5 if w == -1 else 1.0 if w == mine else 0.0
    if not with_vp:
        return base
    margin = (vb - vr) if blue else (vr - vb)
    return base + p.vp_weight * math.tanh(margin / p.vp_scale)


class _Runner:
    """Runs jobs in order, in-process (1 worker) or in a spawn pool; results
    come back in job order either way."""

    def __init__(self, workers: int) -> None:
        self.workers = max(1, workers)
        self.pool = mp.get_context("spawn").Pool(self.workers) if self.workers > 1 else None

    def run(self, jobs: Sequence[tuple], on_done: Callable[[int], None]) -> List[tuple]:
        out = []
        it = map(run_job, jobs) if self.pool is None else self.pool.imap(run_job, jobs, chunksize=1)
        for i, r in enumerate(it, 1):
            out.append(r)
            on_done(i)
        return out

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool.join()


# -- files ------------------------------------------------------------------

def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def gen_files(run_dir: Path) -> List[Path]:
    return sorted(run_dir.glob("gen_[0-9][0-9][0-9].json"))


def load_gen(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Status:
    """status.json, rewritten atomically so the UI can poll it any time."""

    def __init__(self, run_dir: Path, name: str, preset: str, generations: int) -> None:
        self.path = run_dir / "status.json"
        self.d = {"run": name, "preset": preset, "state": "starting", "gen": 0,
                  "generations": generations, "done": 0, "total": 0, "eta_s": None,
                  "elapsed_s": 0.0}
        self.t0 = time.time()
        self.gen_times: List[float] = []
        self.gen_t0 = self.t0

    def write(self, **kw) -> None:
        self.d.update(kw)
        self.d["elapsed_s"] = round(time.time() - self.t0, 1)
        self.d["updated"] = round(time.time(), 1)
        _write_json(self.path, self.d)

    def start_gen(self, g: int, total: int) -> None:
        self.gen_t0 = time.time()
        self.write(state="running", gen=g, done=0, total=total, eta_s=self._eta(g, 0.0))

    def progress(self, done: int) -> None:
        d = self.d
        frac = done / d["total"] if d["total"] else 0.0
        self.write(done=done, eta_s=self._eta(d["gen"], frac))

    def end_gen(self) -> None:
        self.gen_times.append(time.time() - self.gen_t0)

    def _eta(self, g: int, frac: float) -> Optional[float]:
        if not self.gen_times and frac <= 0:
            return None
        per = (sum(self.gen_times) / len(self.gen_times)) if self.gen_times else \
            (time.time() - self.gen_t0) / max(frac, 1e-9)
        left = self.d["generations"] - g - frac
        return round(max(0.0, per * left), 1)


# -- the generation loop ------------------------------------------------------

def _initial_population(seed: int, p: P.EvoParams) -> List[DoctrineGenome]:
    rng = make_rng(seed, "init")
    base = DoctrineGenome.default()
    return [base] + [base.mutate(rng, p.mut_prob, p.mut_sigma * 3.0) for _ in range(p.pop_size - 1)]


def _schedule(seed: int, g: int, pop: List[DoctrineGenome], hof: List[DoctrineGenome],
              maps: List[int], p: P.EvoParams):
    """Jobs for one generation. Returns (jobs, entries); entry = (a, b, kind), a/b = population
    index, hof index or anchor name. Genome `a` is BLUE on even games of its own list."""
    rng = make_rng(seed, "sched", g)
    jobs, entries = [], []
    n = len(pop)
    gspec = [f"genome:{x.to_json()}" for x in pop]
    hspec = [f"genome:{x.to_json()}" for x in hof]
    for i in range(n):
        c = 0
        plan: List[Tuple[str, int]] = [("peer", k) for k in range(p.n_peer if n > 1 else 0)]
        plan += [("hof", k) for k in range(p.n_hof)] + [("anchor", k) for k in range(p.n_anchor)]
        for kind, _ in plan:
            if kind == "hof" and not hof:
                kind = "anchor"
            if kind == "peer":
                j = rng.choice([x for x in range(n) if x != i])
                opp, ref = gspec[j], j
            elif kind == "hof":
                j = rng.randrange(len(hof))
                opp, ref = hspec[j], j
            else:
                ref = rng.choice(P.EVO_ANCHORS)
                opp = f"anchor:{ref}"
            m = maps[rng.randrange(len(maps))]
            gs = derive_seed(seed, "game", g, len(jobs)) % SEED_MOD
            blue = c % 2 == 0
            c += 1
            jobs.append((gspec[i], opp, p.world, m, gs) if blue else (opp, gspec[i], p.world, m, gs))
            entries.append((i, ref, kind, blue))
    return jobs, entries


def _select(rng, fitness: List[float], k: int) -> int:
    idx = rng.sample(range(len(fitness)), min(k, len(fitness)))
    return max(idx, key=lambda i: (fitness[i], -i))


def run_generation(seed: int, g: int, pop: List[DoctrineGenome], hof: List[DoctrineGenome],
                   prev_champ: DoctrineGenome, p: P.EvoParams, runner: _Runner,
                   status: Optional[Status], replay_dir: Optional[Path]) -> dict:
    maps = [derive_seed(seed, "maps", g, i) % MAP_MOD for i in range(p.maps_per_gen)]
    jobs, entries = _schedule(seed, g, pop, hof, maps, p)
    total = len(jobs) + p.anchor_eval_games
    if status:
        status.start_gen(g, total)
    every = max(1, p.status_every)
    prog = (lambda d: status.progress(d) if status and d % every == 0 else None)
    results = runner.run(jobs, prog)

    scores: List[List[float]] = [[] for _ in pop]
    causes: Counter = Counter()
    for (a, b, kind, a_blue), res in zip(entries, results):
        scores[a].append(_score(res, a_blue, p))
        if kind == "peer":
            scores[b].append(_score(res, not a_blue, p))
        causes[res[4]] += 1
    fitness = [round(sum(s) / len(s), 6) if s else 0.0 for s in scores]
    champ = max(range(len(pop)), key=lambda i: (fitness[i], -i))

    # Elo estimate: extra champion games against the fixed anchors.
    cspec = f"genome:{pop[champ].to_json()}"
    ejobs, enames = [], []
    for k in range(p.anchor_eval_games):
        name = P.EVO_ANCHORS[k % len(P.EVO_ANCHORS)]
        m = maps[k % len(maps)]
        gs = derive_seed(seed, "elo", g, k) % SEED_MOD
        ejobs.append((cspec, f"anchor:{name}", p.world, m, gs) if k % 2 == 0
                     else (f"anchor:{name}", cspec, p.world, m, gs))
        enames.append(name)
    eres = runner.run(ejobs, lambda d: prog(len(jobs) + d))
    elo_in = []
    per_anchor: Dict[str, List[float]] = {n: [] for n in P.EVO_ANCHORS}
    for k, (name, res) in enumerate(zip(enames, eres)):
        s = _score(res, k % 2 == 0, p, with_vp=False)
        elo_in.append((P.EVO_ANCHOR_ELO[name], s))
        per_anchor[name].append(s)
        causes[res[4]] += 1
    elo = performance_rating(elo_in)

    # Next generation: elites, then tournament -> crossover -> mutation.
    order = sorted(range(len(pop)), key=lambda i: (-fitness[i], i))
    nxt = [pop[i] for i in order[:p.elites]]
    rng = make_rng(seed, "breed", g)
    while len(nxt) < len(pop):
        a, b = pop[_select(rng, fitness, p.tourn_size)], pop[_select(rng, fitness, p.tourn_size)]
        nxt.append(DoctrineGenome.crossover(a, b, rng, p.cx_prob).mutate(rng, p.mut_prob, p.mut_sigma))
    new_hof = (hof + [pop[champ]])[-p.hof_size:]

    out = {
        "gen": g, "maps": maps, "games": len(jobs) + len(ejobs),
        "population": [x.to_dict() for x in pop], "fitness": fitness, "champion": champ,
        "champion_genome": pop[champ].to_dict(),
        "trait_stats": {t: [round(m, 6), round(s, 6)] for t, (m, s) in
                        trait_stats([x.to_dict() for x in pop]).items()},
        "elo": {"rating": round(elo, 1), "games": len(elo_in),
                "score": round(sum(s for _, s in elo_in), 3),
                "per_anchor": {n: [round(sum(v), 3), len(v)] for n, v in per_anchor.items()}},
        "outcomes": dict(sorted(causes.items())),
        "next_population": [x.to_dict() for x in nxt],
        "hof": [x.to_dict() for x in new_hof],
    }
    if replay_dir is not None:
        _save_replay(seed, g, pop[champ], prev_champ, p, maps[0], replay_dir)
    return out


def _save_replay(seed: int, g: int, champ: DoctrineGenome, prev: DoctrineGenome,
                 p: P.EvoParams, map_seed: int, replay_dir: Path) -> None:
    from ..record.replay import record_game
    replay_dir.mkdir(parents=True, exist_ok=True)
    _, rep = record_game(Commander(champ), Commander(prev), _world(p.world, map_seed),
                         seed=derive_seed(seed, "replay", g) % SEED_MOD,
                         blue_name=f"champion g{g}", red_name=f"champion g{g - 1}" if g else "default")
    rep.save(replay_dir / f"gen_{g:03d}.fbr")


def cross_generation(seed: int, run_dir: Path, p: P.EvoParams, runner: _Runner) -> dict:
    files = gen_files(run_dir)
    gens = [i for i in range(len(files)) if i % p.xgen_stride == 0]
    if gens and gens[-1] != len(files) - 1:
        gens.append(len(files) - 1)
    champs = [f"genome:{DoctrineGenome.from_dict(load_gen(files[i])['champion_genome']).to_json()}"
              for i in gens]
    jobs, keys = [], []
    for a in range(len(gens)):
        for b in range(a + 1, len(gens)):
            for k in range(p.xgen_games):
                m = derive_seed(seed, "xmaps", a, b, k) % MAP_MOD
                gs = derive_seed(seed, "xgame", a, b, k) % SEED_MOD
                jobs.append((champs[a], champs[b], p.world, m, gs) if k % 2 == 0
                            else (champs[b], champs[a], p.world, m, gs))
                keys.append((a, b, k % 2 == 0))
    res = runner.run(jobs, lambda d: None)
    acc: Dict[Tuple[int, int], List[float]] = {}
    for (a, b, a_blue), r in zip(keys, res):
        s = _score(r, a_blue, p, with_vp=False)
        acc.setdefault((a, b), []).append(s)
        acc.setdefault((b, a), []).append(1.0 - s)
    return {"gens": gens, "matrix": winrate_matrix(acc, len(gens)), "games_per_pair": p.xgen_games}


def evolve(preset: str, run: str, seed: int, root: str = DEFAULT_ROOT, resume: bool = False,
           workers: Optional[int] = None, stop_after: Optional[int] = None,
           log: Callable[[str], None] = print, overrides: Optional[dict] = None) -> Path:
    """Run (or resume) an evolution. `stop_after` stops once that many generations exist (tests)."""
    p = P.EVO_PRESETS[preset]
    if overrides:
        p = replace(p, **overrides)
    if workers is not None:
        p = replace(p, workers=workers)
    run_dir = Path(root) / run
    meta_path = run_dir / "meta.json"
    meta = {"preset": preset, "seed": seed,
            "params": {k: v for k, v in asdict(p).items() if k != "workers"}}
    if meta_path.exists():
        if not resume:
            raise SystemExit(f"run '{run}' already exists; use --resume to continue it")
        if json.loads(meta_path.read_text()) != json.loads(json.dumps(meta)):
            raise SystemExit("run parameters differ from the saved meta.json; cannot resume")
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(meta_path, meta)

    files = gen_files(run_dir)
    if files:
        last = load_gen(files[-1])
        pop = [DoctrineGenome.from_dict(d) for d in last["next_population"]]
        hof = [DoctrineGenome.from_dict(d) for d in last["hof"]]
        prev = DoctrineGenome.from_dict(last["champion_genome"])
        start = last["gen"] + 1
        log(f"resuming after generation {last['gen']}")
    else:
        pop, hof, prev, start = _initial_population(seed, p), [], DoctrineGenome.default(), 0

    status = Status(run_dir, run, preset, p.generations)
    runner = _Runner(p.workers)
    try:
        for g in range(start, p.generations):
            res = run_generation(seed, g, pop, hof, prev, p, runner, status, run_dir / "replays")
            _write_json(run_dir / f"gen_{g:03d}.json", res)
            status.end_gen()
            pop = [DoctrineGenome.from_dict(d) for d in res["next_population"]]
            hof = [DoctrineGenome.from_dict(d) for d in res["hof"]]
            prev = DoctrineGenome.from_dict(res["champion_genome"])
            log(f"gen {g}: best fitness {max(res['fitness']):.3f}  mean {mean_std(res['fitness'])[0]:.3f}"
                f"  champion Elo {res['elo']['rating']:.0f}")
            if stop_after is not None and g + 1 >= stop_after and g + 1 < p.generations:
                status.write(state="paused")
                return run_dir
        if not (run_dir / "xgen.json").exists() and len(gen_files(run_dir)) == p.generations:
            status.write(state="finishing")
            _write_json(run_dir / "xgen.json", cross_generation(seed, run_dir, p, runner))
        status.write(state="done", done=status.d["total"], eta_s=0.0)
    finally:
        runner.close()
    return run_dir


# -- bench ----------------------------------------------------------------------

def bench(preset: str, workers: Optional[int] = None, maps: int = 4,
          log: Callable[[str], None] = print, overrides: Optional[dict] = None,
          calibrate: bool = True) -> Dict[str, float]:
    """Time games, estimate the run, and calibrate anchor Elo (default = 1500)."""
    p = P.EVO_PRESETS[preset]
    if overrides:
        p = replace(p, **overrides)
    w = workers or p.workers
    runner = _Runner(w)
    try:
        t = time.time()
        jobs = [("anchor:doctrine-default", "anchor:doctrine-default", p.world,
                 derive_seed("bench", i) % MAP_MOD, i) for i in range(max(2, w))]
        runner.run(jobs, lambda d: None)
        per_batch = time.time() - t
        log(f"{len(jobs)} games on {w} workers: {per_batch:.1f} s "
            f"(~{per_batch / math.ceil(len(jobs) / w):.1f} s per game per worker)")
        games_gen = p.pop_size * (p.n_peer + p.n_hof + p.n_anchor) - \
            p.pop_size * p.n_peer // 2 + p.anchor_eval_games      # peer games count for two genomes
        t_game = per_batch / math.ceil(len(jobs) / w)             # wall seconds per game per worker
        gen_s = math.ceil(games_gen / w) * t_game * 1.1
        log(f"{p.pop_size * (p.n_peer + p.n_hof + p.n_anchor)} genome-games per generation "
            f"(~{games_gen} scheduled), ~{gen_s:.0f} s per generation, "
            f"~{gen_s * p.generations / 60:.0f} min for {p.generations} generations")
        out = {"doctrine-default": 1500.0}
        for name in () if not calibrate else ("rush", "doctrine-legacy"):
            js = []
            for i in range(maps):
                m = derive_seed("calib", i) % MAP_MOD
                js.append(("anchor:doctrine-default", f"anchor:{name}", p.world, m, i))
                js.append((f"anchor:{name}", "anchor:doctrine-default", p.world, m, i))
            res = runner.run(js, lambda d: None)
            pts = [_score(r, k % 2 == 0, p, with_vp=False) for k, r in enumerate(res)]
            s = min(max(sum(pts), 0.5), len(pts) - 0.5) / len(pts)       # default's score
            out[name] = round(1500.0 - 400.0 * math.log10(s / (1.0 - s)), 0)
            log(f"calibration {name}: default scores {sum(pts) / len(pts):.2f} -> Elo {out[name]:.0f}")
        log("paste into params.EVO_ANCHOR_ELO: " + json.dumps(out))
        return out
    finally:
        runner.close()
