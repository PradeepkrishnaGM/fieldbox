"""Telemetry: record a match to structured rows.

Everything the diagnostics and the viewer need comes from here, so there
is exactly one definition of "what happened in this game". The recorder
attaches to a running `Game` and snapshots state after every side-turn; it
never changes behaviour, so a recorded match is bit-identical to an
unrecorded one (asserted in the tests).

Three tables, because they answer different questions:

* `units`  -- one row per unit per side-turn. Position, strength, supply.
* `turns`  -- one row per side-turn. Score, force totals, supply coverage.
* `combat` -- one row per engagement. Who shot whom, at what odds.
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from ..core.hexgrid import Domain
from ..sim.units import Side


@dataclass
class UnitRow:
    turn: int
    phase: int          # side-turn index, so BLUE and RED rows stay ordered
    acting_side: str
    uid: int
    side: str
    utype: str
    domain: str
    q: int
    r: int
    col: int            # column from BLUE's baseline: how far it has advanced
    strength: float
    supply: float
    in_supply: int
    entrench: int
    on_objective: int


@dataclass
class TurnRow:
    turn: int
    phase: int
    acting_side: str
    vp_blue: float
    vp_red: float
    units_blue: int
    units_red: int
    strength_blue: float
    strength_red: float
    supply_blue: float
    supply_red: float
    cutoff_blue: int
    cutoff_red: int
    net_blue: int       # hexes BLUE can trace land supply to
    net_red: int
    engagements: int


@dataclass
class CombatRow:
    turn: int
    phase: int
    attacker_uid: int
    attacker_type: str
    attacker_side: str
    defender_uid: int
    defender_type: str
    defender_side: str
    odds: float
    attacker_loss: float
    defender_loss: float
    destroyed: int


@dataclass
class Recorder:
    """Collects rows from a game in progress."""
    units: List[UnitRow] = field(default_factory=list)
    turns: List[TurnRow] = field(default_factory=list)
    combat: List[CombatRow] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)
    _phase: int = 0
    _combat_seen: int = 0
    _types: Dict[int, tuple] = field(default_factory=dict)

    def capture(self, game, acting_side: Side) -> None:
        """Snapshot after `acting_side` has completed its turn."""
        phase = self._phase
        self._phase += 1

        for u in game.units.values():
            self._types[u.uid] = (u.stats.label, u.side.name)

        for u in sorted(game.units.values(), key=lambda x: x.uid):
            if not u.alive:
                continue
            self.units.append(UnitRow(
                turn=game.turn, phase=phase, acting_side=acting_side.name,
                uid=u.uid, side=u.side.name, utype=u.stats.label,
                domain=u.domain.name, q=u.q, r=u.r, col=u.q + u.r // 2,
                strength=round(u.strength, 2), supply=round(u.supply, 2),
                in_supply=int(u.in_supply), entrench=u.entrench,
                on_objective=int(
                    u.coord in game.objectives and u.stats.can_capture
                ),
            ))

        blue = game.units_of(Side.BLUE)
        red = game.units_of(Side.RED)
        nets = game.supply_net
        self.turns.append(TurnRow(
            turn=game.turn, phase=phase, acting_side=acting_side.name,
            vp_blue=game.vp[Side.BLUE], vp_red=game.vp[Side.RED],
            units_blue=len(blue), units_red=len(red),
            strength_blue=round(sum(u.strength for u in blue), 1),
            strength_red=round(sum(u.strength for u in red), 1),
            supply_blue=round(_mean(u.supply for u in blue), 1),
            supply_red=round(_mean(u.supply for u in red), 1),
            cutoff_blue=sum(1 for u in blue if not u.in_supply),
            cutoff_red=sum(1 for u in red if not u.in_supply),
            net_blue=len(nets.get(Side.BLUE, {}).get(Domain.LAND, ())),
            net_red=len(nets.get(Side.RED, {}).get(Domain.LAND, ())),
            engagements=len(game.combat_log),
        ))

        for cr in game.combat_log[self._combat_seen:]:
            a_type, a_side = self._types.get(cr.attacker, ("?", "?"))
            d_type, d_side = self._types.get(cr.defender, ("?", "?"))
            self.combat.append(CombatRow(
                turn=game.turn, phase=phase,
                attacker_uid=cr.attacker, attacker_type=a_type,
                attacker_side=a_side,
                defender_uid=cr.defender, defender_type=d_type,
                defender_side=d_side,
                odds=round(cr.odds, 3),
                attacker_loss=round(cr.attacker_loss, 2),
                defender_loss=round(cr.defender_loss, 2),
                destroyed=int(cr.defender_destroyed),
            ))
        self._combat_seen = len(game.combat_log)

    # -- output -----------------------------------------------------------

    def write_csv(self, outdir: str, prefix: str = "") -> List[str]:
        os.makedirs(outdir, exist_ok=True)
        written = []
        for name, rows in (
            ("turns", self.turns), ("units", self.units), ("combat", self.combat)
        ):
            path = os.path.join(outdir, f"{prefix}{name}.csv")
            with open(path, "w", newline="") as fh:
                if not rows:
                    fh.write("")
                    written.append(path)
                    continue
                writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0])))
                writer.writeheader()
                for row in rows:
                    writer.writerow(asdict(row))
            written.append(path)
        return written


def _mean(values) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def play_recorded(blue, red, scenario, seed: int = 0, max_steps: int = 10_000):
    """Run a match and return (game, recorder).

    Recording is pure observation, so this produces exactly the same game as
    `arena.play_match` with the same seed.
    """
    from ..sim.engine import Game

    game = Game(scenario, seed=seed)
    blue.reset(game, Side.BLUE)
    red.reset(game, Side.RED)
    agents = {Side.BLUE: blue, Side.RED: red}
    rec = Recorder()
    rec.meta = {
        "seed": str(seed),
        "map_seed": str(getattr(scenario, "map_seed", "")),
        "template": str(getattr(scenario, "template", "")),
        "max_turns": str(scenario.max_turns),
    }

    # Phase 0 is the deployment, captured before anyone moves. Without it
    # "start hex" meant "position after the first turn", which reported a
    # unit that advanced immediately as having never left its start.
    rec.capture(game, Side.BLUE)

    steps = 0
    while not game.is_over and steps < max_steps:
        side = game.current_side
        game.step(agents[side].act(game, side))
        rec.capture(game, side)
        steps += 1

    winner = game.winner()
    rec.meta["winner"] = winner.name if winner is not None else "DRAW"
    rec.meta["turns"] = str(game.turn)
    return game, rec
