"""Replays (PLAN.md M5): a snapshot per turn plus the turn's events.

A replay holds no engine state, only plain data, so the viewer can scrub
through it without running anything. The static map is not stored: the world
is regenerated from (preset, seed) and checked against a stored fingerprint,
since generation is deterministic.

File format `.fbr`: gzip JSON, top-level `version`. Snapshot k is the state
after k turns (0 = deployment); `events[k]` are the events that produced it.
Unit rows: [uid, side, type, q, r, strength, supply, in_supply, entrench,
order]; structure rows: [sid, kind, side, q, r, strength]. `vis`, `ghosts` and
`known` hold each side's fog-of-war view (absent in version 1 files).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..sim.units import Side

VERSION = 2          # 2 adds per-side visibility, ghosts and known structures (fog of war)
READABLE = (1, 2)


class ReplayError(Exception):
    pass


def _order_text(o) -> str:
    """Short text for a standing order, for the viewer's selection card."""
    if o is None:
        return ""
    n = type(o).__name__
    if n == "MoveTo":
        return f"move {o.dest[0]},{o.dest[1]}"
    if n == "Attack":
        return f"attack S{o.target}" if o.structure else f"attack #{o.target}"
    if n == "Bombard":
        return f"bombard {o.dest[0]},{o.dest[1]}"
    if n == "Strike":
        return f"strike S{o.sid}"
    if n == "Construct":
        return f"build {o.kind.name}"
    if n == "Repair":
        return f"repair S{o.sid}"
    return n.lower()


def snapshot(game) -> dict:
    """Plain-data state of a Game (units, structures, owners, economy)."""
    sides = (Side.BLUE, Side.RED)
    t = game.turn
    return {
        "turn": game.turn,
        "units": [[u.uid, int(u.side), u.utype.name, u.q, u.r, round(u.strength, 2),
                   round(u.supply, 1), int(u.in_supply), u.entrench,
                   _order_text(game.standing.get(u.uid))]
                  for u in sorted(game.units.values(), key=lambda x: x.uid)],
        "structures": [[s.sid, s.kind.name, int(s.side), s.q, s.r, round(s.strength, 2)]
                       for s in sorted(game.structures.values(), key=lambda x: x.sid)],
        "queues": {str(sid): [[i.utype.name, i.left] for i in q]
                   for sid, q in sorted(game.queues.items()) if q},
        "owner": [(-1 if game.owner.get(p.id) is None else int(game.owner[p.id]))
                  for p in game.world.provinces],
        "vp": [game.vp[s] for s in sides],
        "pp": [round(game.pp[s], 1) for s in sides],
        "income": [round(game.income(s), 1) for s in sides],
        "cp": [game.cp(s) for s in sides],
        "power": [round(game.power[s], 3) for s in sides],
        # Fog of war, per side: [land-visible hexes, air/sea-visible hexes].
        "vis": [[sorted(map(list, game.vis[s].land)), sorted(map(list, game.vis[s].air))]
                for s in sides],
        "ghosts": [[[g.uid, g.utype.name, g.q, g.r, round(g.strength, 1), t - g.seen]
                    for g in game.fog[s].ghosts(t)] for s in sides],
        "known": [[[r.sid, r.kind.name, r.side, r.q, r.r, round(r.strength, 1), t - r.seen]
                   for _, r in sorted(game.fog[s].structures.items())] for s in sides],
    }


@dataclass
class Replay:
    meta: Dict[str, object] = field(default_factory=dict)
    snapshots: List[dict] = field(default_factory=list)
    events: List[List[dict]] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return "result" in self.meta

    def __len__(self) -> int:
        return len(self.snapshots)

    # -- files ------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"version": VERSION, "meta": self.meta, "snapshots": self.snapshots,
                "events": self.events}

    def save(self, path) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, sort_keys=True, separators=(",", ":"))
        return str(p)

    @classmethod
    def load(cls, path) -> "Replay":
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ReplayError(f"cannot read replay {path}: {exc}") from exc
        if d.get("version") not in READABLE:
            raise ReplayError(f"unsupported replay version {d.get('version')!r} "
                              f"(readable: {READABLE})")
        return cls(d["meta"], d["snapshots"], d["events"])

    def world(self):
        """Regenerate the static map and verify it is the one that was played."""
        from ..world import generate_world
        w = generate_world(int(self.meta["map_seed"]), str(self.meta["preset"]))
        if w.fingerprint() != self.meta.get("fingerprint"):
            raise ReplayError("the generated map no longer matches this replay "
                              "(generator changed since it was recorded)")
        return w


class LiveMatch:
    """A Game stepped one turn at a time with two agents, recording as it goes.

    The match screen drives this in live mode; `record_game` runs it to the
    end. Recording is pure observation: the game is identical to one played
    without it.
    """

    def __init__(self, blue, red, world, seed: int = 0, max_turns: Optional[int] = None,
                 blue_name: str = "blue", red_name: str = "red") -> None:
        from ..sim.engine import Game
        self.game = Game(world, seed=seed, max_turns=max_turns)
        self.agents = {Side.BLUE: blue, Side.RED: red}
        for side, agent in self.agents.items():
            agent.reset(side, world)
        self.replay = Replay(meta={
            "blue": blue_name, "red": red_name, "seed": seed, "map_seed": world.seed,
            "preset": world.preset, "max_turns": self.game.max_turns,
            "fingerprint": world.fingerprint()})
        self.replay.snapshots.append(snapshot(self.game))
        self.replay.events.append([])
        self._seen = 0

    @property
    def done(self) -> bool:
        return self.game.is_over

    def step(self) -> None:
        g = self.game
        if g.is_over:
            return
        g.step_turn({s: self.agents[s].act(g.observe(s)) for s in (Side.BLUE, Side.RED)})
        self.replay.snapshots.append(snapshot(g))
        self.replay.events.append([json.loads(e.to_json()) for e in g.events[self._seen:]])
        self._seen = len(g.events)
        if g.is_over:
            w = g.winner()
            self.replay.meta["result"] = {
                "winner": w.name if w is not None else "DRAW", "cause": g.result[1],
                "turns": g.turn, "vp": [g.vp[Side.BLUE], g.vp[Side.RED]]}


def record_game(blue, red, world, seed: int = 0, max_turns: Optional[int] = None,
                blue_name: Optional[str] = None, red_name: Optional[str] = None):
    """Play a whole match; returns (game, replay)."""
    m = LiveMatch(blue, red, world, seed, max_turns,
                  blue_name or getattr(blue, "name", "blue"),
                  red_name or getattr(red, "name", "red"))
    while not m.done:
        m.step()
    return m.game, m.replay
