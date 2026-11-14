"""Evolution Lab: start, watch and inspect evolution runs.

The screen never imports the league code that runs games. A run is a
`python -m fieldbox evolve ...` subprocess; this screen polls its files
(`status.json`, `gen_XXX.json`, `xgen.json`) once a second.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pygame

from .. import charts, theme
from ..app import Screen
from ..widgets import Button
from ... import params as P
from ...agents.genome import TRAITS
from ...sim.units import Side

ROOT = "runs/evo"
POLL_S = 1.0
GROUP = 6                       # traits per chart page
LEFT_W, CARD_W = 300, 340
DEFAULTS = {k: P.GENOME_DEFAULTS.get(k, 0.5) for k in TRAITS}
OUTCOME_KEYS = ("hq_destroyed", "annihilation", "turn_limit")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fmt_eta(s: Optional[float]) -> str:
    if s is None:
        return "ETA: estimating..."
    s = int(s)
    return f"ETA {s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"ETA {s // 60}m {s % 60:02d}s"


class EvoLabScreen(Screen):
    def __init__(self, app, root: str = ROOT, run: Optional[str] = None) -> None:
        super().__init__(app)
        self.root = Path(root)
        self.runs: List[str] = []
        self.run: Optional[str] = run
        self.meta: dict = {}
        self.gens: List[dict] = []
        self.status: dict = {}
        self.xgen: Optional[dict] = None
        self.sel_gen: Optional[int] = None       # None = latest
        self.page = 0                            # trait chart page
        self.proc: Optional[subprocess.Popen] = None
        self.est_proc: Optional[subprocess.Popen] = None
        self.est_text = ""
        self._poll = 0.0
        self._plots: Dict[str, pygame.Rect] = {}
        self._card_rects: Dict[str, pygame.Rect] = {}
        # new-run form
        self.f_preset = "evo-tiny"
        self.f_pop: Optional[int] = None
        self.f_gens: Optional[int] = None
        self.f_workers: Optional[int] = None
        self.f_seed = 1
        self.buttons: List[Button] = []
        self._live_buttons: List[Button] = []
        self._form_buttons: List[Button] = []
        self.refresh()

    # -- data ---------------------------------------------------------------

    def preset(self) -> P.EvoParams:
        return P.EVO_PRESETS[self.f_preset]

    def refresh(self) -> None:
        self.runs = []
        if self.root.exists():
            dirs = [d for d in self.root.iterdir() if (d / "meta.json").exists()]
            self.runs = [d.name for d in sorted(dirs, key=lambda d: -d.stat().st_mtime)]
        if self.run is None and self.runs:
            self.run = self.runs[0]
        if self.run is None:
            self.meta, self.gens, self.status, self.xgen = {}, [], {}, None
            return
        d = self.root / self.run
        self.meta = _read(d / "meta.json") or {}
        self.status = _read(d / "status.json") or {}
        files = sorted(d.glob("gen_[0-9][0-9][0-9].json"))
        if len(files) != len(self.gens):
            self.gens = [g for g in (_read(f) for f in files) if g]
        self.xgen = _read(d / "xgen.json")

    def select_run(self, name: str) -> None:
        self.run, self.gens, self.sel_gen, self.xgen = name, [], None, None
        self.refresh()

    @property
    def gen(self) -> Optional[int]:
        if not self.gens:
            return None
        return min(self.sel_gen if self.sel_gen is not None else len(self.gens) - 1, len(self.gens) - 1)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- actions --------------------------------------------------------------

    def next_name(self) -> str:
        k = 1
        while f"run{k}" in self.runs:
            k += 1
        return f"run{k}"

    def _cmd(self, extra: List[str]) -> List[str]:
        p = self.preset()
        cmd = [sys.executable, "-m", "fieldbox", "evolve", "--preset", self.f_preset,
               "--root", str(self.root), "--seed", str(self.f_seed)]
        if self.f_pop:
            cmd += ["--pop", str(self.f_pop)]
        if self.f_gens:
            cmd += ["--generations", str(self.f_gens)]
        cmd += ["--workers", str(self.f_workers or p.workers)]
        return cmd + extra

    def start_run(self) -> None:
        if self.running():
            return
        name = self.next_name()
        self.root.mkdir(parents=True, exist_ok=True)
        log = open(self.root / f"{name}.log", "w")
        self.proc = subprocess.Popen(self._cmd(["--run", name]), stdout=log, stderr=subprocess.STDOUT)
        time.sleep(0.5)
        self.select_run(name)

    def resume_run(self) -> None:
        if self.running() or not self.run or self.status.get("state") == "done":
            return
        m = self.meta
        cmd = [sys.executable, "-m", "fieldbox", "evolve", "--preset", m.get("preset", "evo-tiny"),
               "--root", str(self.root), "--seed", str(m.get("seed", 1)), "--run", self.run, "--resume",
               "--pop", str(m["params"]["pop_size"]), "--generations", str(m["params"]["generations"])]
        log = open(self.root / f"{self.run}.log", "a")
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)

    def estimate(self) -> None:
        if self.est_proc is not None and self.est_proc.poll() is None:
            return
        self.est_text = "benchmarking..."
        self.est_proc = subprocess.Popen(self._cmd(["--bench", "--quick"]), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)

    def stop_run(self) -> None:
        if self.running():
            self.proc.terminate()

    def watch_replay(self) -> None:
        g = self.gen
        path = self.root / (self.run or "") / "replays" / f"gen_{(g or 0):03d}.fbr"
        if g is None or not path.exists():
            return
        from ...record.replay import Replay
        from .match import MatchScreen
        rep = Replay.load(path)
        self.app.push(MatchScreen(self.app, rep.world(), rep, None))

    def play_vs_default(self) -> None:
        g = self.gen
        if g is None:
            return
        from ...agents import Commander, DoctrineGenome
        from ...record.replay import LiveMatch
        from ...world import generate_world
        from .match import MatchScreen
        world = generate_world(1, self.meta.get("params", {}).get("world", "tiny"))
        genome = DoctrineGenome.from_dict(self.gens[g]["champion_genome"])
        live = LiveMatch(Commander(genome), Commander(), world, seed=0,
                         blue_name=f"champion g{g}", red_name="default")
        self.app.push(MatchScreen(self.app, world, live.replay, live))

    def _run_rows(self) -> List[pygame.Rect]:
        return [pygame.Rect(10, 52 + i * 22, LEFT_W - 20, 20) for i in range(8)]

    def update(self, dt: float) -> None:
        self._poll += dt
        if self.est_proc is not None and self.est_proc.poll() is not None:
            out = (self.est_proc.stdout.read() or "").strip().splitlines()
            self.est_text = "\n".join(l for l in out if "generation" in l or "Error" in l)[:400] or "no result"
            self.est_proc = None
        if self._poll >= POLL_S:
            self._poll = 0.0
            self.refresh()

    # -- drawing --------------------------------------------------------------

    def _stepper(self, surf, y: int, label: str, value: str, key: str) -> None:
        surf.blit(theme.font(13).render(label, True, theme.DIM), (14, y + 3))
        surf.blit(theme.font(13).render(value, True, theme.TEXT), (140, y + 3))
        for k, (txt, dx) in enumerate((("-", 210), ("+", 250))):
            self._small(key, txt, pygame.Rect(dx, y, 32, 22), -1 if txt == "-" else 1)

    def _small(self, key: str, txt: str, r: pygame.Rect, delta: int) -> None:
        self._form_buttons.append(Button(r, txt, lambda: self._adjust(key, delta)))

    def _adjust(self, key: str, d: int) -> None:
        p = self.preset()
        if key == "page":
            self._page_step(d)
        elif key == "pop":
            self.f_pop = max(2, (self.f_pop or p.pop_size) + 2 * d)
        elif key == "gens":
            self.f_gens = max(1, (self.f_gens or p.generations) + d)
        elif key == "workers":
            self.f_workers = max(1, min(12, (self.f_workers or p.workers) + d))
        elif key == "seed":
            self.f_seed = max(0, self.f_seed + d)

    def _toggle_preset(self) -> None:
        names = sorted(P.EVO_PRESETS)
        self.f_preset = names[(names.index(self.f_preset) + 1) % len(names)]
        self.f_pop = self.f_gens = self.f_workers = None

    def _page_step(self, d: int) -> None:
        self.page = (self.page + d) % ((len(TRAITS) + GROUP - 1) // GROUP)

    def draw(self, surf: pygame.Surface) -> None:
        W, H = surf.get_size()
        self._form_buttons: List[Button] = []
        self._plots = {}
        self._draw_left(surf, H)
        cx0, cx1 = LEFT_W + 10, W - CARD_W - 10
        self._draw_progress(surf, pygame.Rect(cx0, 10, cx1 - cx0, 52))
        area = pygame.Rect(cx0, 70, cx1 - cx0, H - 80)
        cw, ch = (area.w - 10) // 2, (area.h - 10) // 2
        mark = self.gen
        n = len(self.gens)
        # 1 trait means
        r1 = pygame.Rect(area.x, area.y, cw, ch)
        names = TRAITS[self.page * GROUP:(self.page + 1) * GROUP]
        series = {t.replace("w_build_", "b:"): [g["trait_stats"][t][0] for g in self.gens] for t in names}
        self._plots["traits"] = charts.line_chart(surf, r1, f"Trait means ({self.page + 1}/"
                                                  f"{(len(TRAITS) + GROUP - 1) // GROUP})",
                                                  series, 0.0, 1.0, mark=mark)
        self._small("page", "<", pygame.Rect(r1.right - 62, r1.y + 3, 24, 20), -1)
        self._small("page", ">", pygame.Rect(r1.right - 34, r1.y + 3, 24, 20), 1)
        # 2 champion Elo vs anchors
        r2 = pygame.Rect(area.x + cw + 10, area.y, cw, ch)
        elo = {"champion": [g["elo"]["rating"] for g in self.gens]}
        for a in P.EVO_ANCHORS:
            elo[a.replace("doctrine-", "")] = [P.EVO_ANCHOR_ELO[a]] * max(n, 1)
        lo = min(min(v) for v in elo.values() if v) - 50 if n else None
        hi = max(max(v) for v in elo.values() if v) + 50 if n else None
        self._plots["elo"] = charts.line_chart(surf, r2, "Champion Elo vs anchors", elo, lo, hi, "{:.0f}",
                                               dashed=[a.replace("doctrine-", "") for a in P.EVO_ANCHORS],
                                               mark=mark)
        # 3 cross-generation matrix
        r3 = pygame.Rect(area.x, area.y + ch + 10, cw, ch)
        charts.heatmap(surf, r3, "Cross-generation win rate (row beats column)",
                       self.xgen["matrix"] if self.xgen else None,
                       [f"g{g}" for g in (self.xgen["gens"] if self.xgen else [])])
        # 4 outcome mix
        r4 = pygame.Rect(area.x + cw + 10, area.y + ch + 10, cw, ch)
        self._plots["outcomes"] = charts.stacked_bar(surf, r4, "Outcome mix per generation",
                                                     [g["outcomes"] for g in self.gens], OUTCOME_KEYS,
                                                     mark=mark)
        self._draw_card(surf, pygame.Rect(W - CARD_W - 5, 10, CARD_W, H - 20))
        for b in self.buttons + self._form_buttons:
            b.draw(surf)
        self._live_buttons = self.buttons + self._form_buttons

    # -- events (buttons are rebuilt every frame, so they are kept in _live_buttons) ----

    def handle_event(self, e) -> None:
        for b in getattr(self, "_live_buttons", []):
            if b.handle_event(e):
                return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.app.pop()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, r in enumerate(self._run_rows()):
                if r.collidepoint(e.pos) and i < len(self.runs):
                    self.select_run(self.runs[i])
                    return
            for key in ("traits", "elo", "outcomes"):
                r = self._plots.get(key)
                if r is not None and r.collidepoint(e.pos):
                    i = charts.index_at(r, len(self.gens), e.pos[0])
                    if i is not None:
                        self.sel_gen = i
                    return

    def _draw_left(self, surf, H) -> None:
        pygame.draw.rect(surf, theme.PANEL, (5, 5, LEFT_W, H - 10))
        pygame.draw.rect(surf, theme.PANEL_BORDER, (5, 5, LEFT_W, H - 10), 1)
        surf.blit(theme.font(15, True).render("EVOLUTION LAB", True, theme.ACCENT), (14, 14))
        surf.blit(theme.font(12).render(f"runs in {self.root}", True, theme.DIM), (14, 34))
        for i, r in enumerate(self._run_rows()):
            if i >= len(self.runs):
                break
            name = self.runs[i]
            if name == self.run:
                pygame.draw.rect(surf, theme.PANEL_BORDER, r)
            surf.blit(theme.font(13).render(name, True, theme.TEXT), (r.x + 4, r.y + 2))
        if not self.runs:
            surf.blit(theme.font(13).render("no runs yet", True, theme.DIM), (14, 56))
        y = 240
        surf.blit(theme.font(14, True).render("NEW RUN", True, theme.ACCENT), (14, y))
        p = self.preset()
        y += 28
        surf.blit(theme.font(13).render("preset", True, theme.DIM), (14, y + 3))
        self._form_buttons.append(Button(pygame.Rect(140, y, 140, 22), self.f_preset, self._toggle_preset))
        rows = (("population", str(self.f_pop or p.pop_size), "pop"),
                ("generations", str(self.f_gens or p.generations), "gens"),
                ("workers", str(self.f_workers or p.workers), "workers"),
                ("run seed", str(self.f_seed), "seed"))
        for label, val, key in rows:
            y += 30
            self._stepper(surf, y, label, val, key)
        y += 34
        self._form_buttons.append(Button(pygame.Rect(14, y, 128, 26), "Estimate ETA", self.estimate))
        label = "Running..." if self.running() else "Start run"
        self._form_buttons.append(Button(pygame.Rect(152, y, 128, 26), label, self.start_run))
        y += 34
        for line in (self.est_text or "ETA from a quick benchmark appears here.").splitlines():
            for chunk in _wrap(line, 40):
                surf.blit(theme.font(12).render(chunk, True, theme.DIM), (14, y))
                y += 15
        yb = H - 80
        self._form_buttons.append(Button(pygame.Rect(14, yb, 128, 26), "Resume run", self.resume_run))
        self._form_buttons.append(Button(pygame.Rect(152, yb, 128, 26), "Stop run", self.stop_run))
        surf.blit(theme.font(12).render("Esc: back", True, theme.DIM), (14, H - 40))

    def _draw_progress(self, surf, rect) -> None:
        pygame.draw.rect(surf, theme.PANEL, rect)
        pygame.draw.rect(surf, theme.PANEL_BORDER, rect, 1)
        st = self.status
        if not st:
            txt = "select or start a run"
            frac = 0.0
        else:
            total, done = st.get("total") or 0, st.get("done") or 0
            gens = st.get("generations", 0)
            frac = ((st.get("gen", 0) + (done / total if total else 0.0)) / gens) if gens else 0.0
            if st.get("state") == "done":
                frac = 1.0
            txt = (f"{st.get('run', '')}  {st.get('state', '')}  generation "
                   f"{min(st.get('gen', 0) + (0 if st.get('state') == 'done' else 1), gens)}/{gens}"
                   f"   games {done}/{total}   {fmt_eta(st.get('eta_s')) if st.get('state') == 'running' else ''}")
        surf.blit(theme.font(14).render(txt, True, theme.TEXT), (rect.x + 10, rect.y + 7))
        bar = pygame.Rect(rect.x + 10, rect.y + 32, rect.w - 20, 10)
        pygame.draw.rect(surf, theme.BG, bar)
        pygame.draw.rect(surf, theme.GOOD, (bar.x, bar.y, int(bar.w * min(frac, 1.0)), bar.h))
        pygame.draw.rect(surf, theme.PANEL_BORDER, bar, 1)

    def _draw_card(self, surf, rect) -> None:
        pygame.draw.rect(surf, theme.PANEL, rect)
        pygame.draw.rect(surf, theme.PANEL_BORDER, rect, 1)
        g = self.gen
        if g is None:
            surf.blit(theme.font(14).render("CHAMPION", True, theme.ACCENT), (rect.x + 10, rect.y + 8))
            surf.blit(theme.font(13).render("no generation finished yet", True, theme.DIM),
                      (rect.x + 10, rect.y + 32))
            return
        d = self.gens[g]
        surf.blit(theme.font(15, True).render(f"CHAMPION  generation {g}", True, theme.ACCENT),
                  (rect.x + 10, rect.y + 8))
        sub = f"fitness {d['fitness'][d['champion']]:.3f}   Elo {d['elo']['rating']:.0f}   (bar | = default)"
        surf.blit(theme.font(12).render(sub, True, theme.DIM), (rect.x + 10, rect.y + 30))
        y = rect.y + 52
        champ = d["champion_genome"]
        track_x, track_w = rect.x + 140, rect.w - 160
        for t in TRAITS:
            v, dv = champ[t], DEFAULTS[t]
            surf.blit(theme.font(11).render(t.replace("w_build_", "build ").replace("_", " "), True,
                                            theme.TEXT), (rect.x + 10, y))
            pygame.draw.rect(surf, theme.BG, (track_x, y + 2, track_w, 9))
            col = theme.SIDE_FILL[Side.BLUE] if v >= dv else theme.WARN
            pygame.draw.rect(surf, col, (track_x, y + 2, int(track_w * v), 9))
            pygame.draw.line(surf, theme.TEXT, (track_x + int(track_w * dv), y), (track_x + int(track_w * dv), y + 12), 1)
            y += 15
        by = rect.bottom - 38
        has = (self.root / (self.run or "") / "replays" / f"gen_{g:03d}.fbr").exists()
        self._form_buttons.append(Button(pygame.Rect(rect.x + 10, by, 150, 28),
                                         "Watch replay" if has else "(no replay)", self.watch_replay))
        self._form_buttons.append(Button(pygame.Rect(rect.x + 170, by, 160, 28), "Play vs default",
                                         self.play_vs_default))


def _wrap(text: str, n: int) -> List[str]:
    out, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])
