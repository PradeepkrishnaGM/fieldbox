"""Batch diagnostics.

Every round of playtesting so far has involved hand-extracting the same
statistics from board dumps: which unit types die, which never fire, which
sit still, whether supply bites, whether one colour is favoured. This does
that automatically over a batch of games and prints it in about fifty lines.

The `ANOMALIES` section is the point. Each check corresponds to a bug class
that actually shipped in this project:

* artillery dying in 30 of 30 games (v0.1.1)
* recon dying in 20 of 20 games (v0.2.0)
* a SAM battery holding position through 32 turns of shellfire (v0.3.0)
* frigates idling in a corner all game (v0.3.0)
* the entrenched defender losing to random play (v0.1.2)
* an objective column one hex nearer RED (v0.2.0)

A passing test suite caught none of them. These checks would have.
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from .. import params as P
from .telemetry import Recorder, play_recorded
from ..sim.units import Side

# Thresholds for the anomaly checks. Deliberately loose: these flag things
# worth looking at, they are not pass/fail assertions.
TOTAL_LOSS = P.REPORT_TOTAL_LOSS
LOSS_ASYMMETRY = P.REPORT_LOSS_ASYMMETRY
# A unit "participates" if it fights OR holds scoring ground. Infantry that
# sits on an objective for thirty turns is doing its job, not idling -- the
# first version of this check flagged it, which was the tool being wrong
# about the game rather than the other way round.
NEVER_FIRES = P.REPORT_NEVER_FIRES
STUCK = P.REPORT_STUCK
CUTOFF_HEAVY = P.REPORT_CUTOFF_HEAVY
COLOUR_SWING = P.REPORT_COLOUR_SWING


@dataclass
class TypeStats:
    count: int = 0
    lost: int = 0
    kills: int = 0
    losses_taken: int = 0
    unit_turns: int = 0
    engagements: int = 0
    objective_turns: int = 0
    cutoff_turns: int = 0
    stuck_turns: int = 0
    end_strength: float = 0.0
    end_count: int = 0


def analyse(records: List[Recorder], side: str = "BLUE") -> Dict[str, TypeStats]:
    stats: Dict[str, TypeStats] = defaultdict(TypeStats)
    for rec in records:
        start_hex: Dict[int, tuple] = {}
        seen: Dict[int, str] = {}
        last_phase = max((r.phase for r in rec.units), default=-1)
        for row in rec.units:
            if row.side != side:
                continue
            st = stats[row.utype]
            if row.uid not in seen:
                seen[row.uid] = row.utype
                st.count += 1
                start_hex[row.uid] = (row.q, row.r)
            st.unit_turns += 1
            if not row.in_supply:
                st.cutoff_turns += 1
            if (row.q, row.r) == start_hex[row.uid]:
                st.stuck_turns += 1
            if getattr(row, "on_objective", 0):
                st.objective_turns += 1
            if row.phase == last_phase:
                st.end_strength += row.strength
                st.end_count += 1

        survivors = {r.uid for r in rec.units if r.phase == last_phase}
        for uid, utype in seen.items():
            if uid not in survivors:
                stats[utype].lost += 1

        for row in rec.combat:
            if row.attacker_side == side:
                stats[row.attacker_type].engagements += 1
                if row.destroyed:
                    stats[row.attacker_type].kills += 1
            if row.defender_side == side and row.destroyed:
                stats[row.defender_type].losses_taken += 1
    return stats


def culmination(records: List[Recorder], side: str = "BLUE") -> List[tuple]:
    """Mean supply by how far a unit has advanced from its own baseline."""
    buckets: Dict[int, List[float]] = defaultdict(list)
    for rec in records:
        for row in rec.units:
            if row.side != side or row.domain != "LAND":
                continue
            advanced = row.col if side == "BLUE" else (18 - row.col)
            buckets[max(0, advanced) // 3].append(row.supply)
    return [
        (b * 3, sum(v) / len(v), len(v))
        for b, v in sorted(buckets.items()) if v
    ]


def find_anomalies(stats: Dict[str, TypeStats], as_blue: float,
                   as_red: float, games: int,
                   opponent: Optional[Dict[str, TypeStats]] = None) -> List[str]:
    """Flags worth investigating.

    Loss rates are judged *against the opponent's*, not against an absolute
    threshold. Both sides losing every tank is a bloody scenario, not a bug;
    one side losing every tank while the other keeps them is a bug. The
    absolute check flagged the harmless case in `littoral` and stayed silent
    on a 83-point gap in `combined_arms`.
    """
    flags = []
    opponent = opponent or {}
    for utype, st in sorted(stats.items()):
        if st.count == 0:
            continue
        loss_rate = st.lost / st.count
        rival = opponent.get(utype)
        rival_rate = rival.lost / rival.count if rival and rival.count else None

        if rival_rate is not None and abs(loss_rate - rival_rate) >= LOSS_ASYMMETRY:
            worse = "BLUE" if loss_rate > rival_rate else "RED"
            flags.append(
                f"{utype}: BLUE loses {loss_rate:.0%}, RED loses "
                f"{rival_rate:.0%} -- {worse} is doing something markedly "
                f"worse with the same unit on the same map"
            )
        elif loss_rate >= TOTAL_LOSS and (rival_rate is None
                                          or rival_rate < TOTAL_LOSS - 0.1):
            flags.append(
                f"{utype}: destroyed in {loss_rate:.0%} of deployments "
                f"({st.lost}/{st.count}) -- positioning or counter-play missing"
            )
        contribution = st.engagements + st.objective_turns
        if st.unit_turns and contribution / st.unit_turns < NEVER_FIRES:
            flags.append(
                f"{utype}: {st.engagements} engagements and "
                f"{st.objective_turns} turns holding ground over "
                f"{st.unit_turns} unit-turns -- neither fighting nor scoring"
            )
        if st.unit_turns and st.stuck_turns / st.unit_turns > STUCK:
            flags.append(
                f"{utype}: spent {st.stuck_turns / st.unit_turns:.0%} of its "
                f"turns on its deployment hex -- likely falling through to Hold"
            )
        if st.unit_turns and st.cutoff_turns / st.unit_turns > CUTOFF_HEAVY:
            flags.append(
                f"{utype}: out of supply for "
                f"{st.cutoff_turns / st.unit_turns:.0%} of its turns"
            )
    swing = abs(as_blue - as_red)
    if swing >= COLOUR_SWING:
        flags.append(
            f"colour dependence: BLUE wins {as_blue:.0%} of the time playing "
            f"first, {as_red:.0%} playing second (swing {swing:.0%}). A "
            f"single-colour batch would report either as 'the' result. "
            f"Elo alternates colours so the ladder is still fair, but this "
            f"matchup is decided more by turn order than by policy."
        )
    return flags


def run(preset: str, blue_name: str, red_name: str, games: int,
        map_seeds=(1, 2, 3, 4), outdir: Optional[str] = None) -> str:
    """Play a batch, return the report text, optionally write CSVs."""
    from ..agents import REGISTRY
    from ..world import generate_world

    def make(name, seed):
        """Construct an agent, seeding only the ones that take a seed.

        The first version did `try: cls(seed) except TypeError: cls()`, which
        silently passed the game index into `DoctrineAgent(aggression=...)`.
        Every batch was therefore running eight *different* doctrines --
        aggression 0 (needs 26:1 to attack, so never attacks) through
        aggression 7 (needs 0.2:1, so always attacks) -- and reporting the
        average as though it were one agent. It is why tanks appeared to die
        in 100% of deployments when the real figure is about half.

        Positional-argument duck typing is a bad way to configure anything.
        """
        cls = REGISTRY[name]
        params = inspect.signature(cls.__init__).parameters
        if "seed" in params:
            return cls(seed=seed)
        return cls()

    # Play every pairing both ways, so colour can never pass for policy
    # strength (it did, badly, under the old sequential turns).
    records: List[Recorder] = []          # records where `blue_name` played BLUE
    swapped: List[Recorder] = []
    wins_as_blue = wins_as_red = 0
    turns_total = 0
    worlds = [generate_world(s, preset) for s in map_seeds]
    half = max(1, games // 2)

    for i in range(half):
        world = worlds[i % len(worlds)]
        game, rec = play_recorded(
            make(blue_name, i), make(red_name, i + 500), world, seed=i
        )
        records.append(rec)
        if rec.meta["winner"] == "BLUE":
            wins_as_blue += 1
        turns_total += game.turn

        game2, rec2 = play_recorded(
            make(red_name, i + 500), make(blue_name, i), world, seed=i
        )
        swapped.append(rec2)
        if rec2.meta["winner"] == "RED":
            wins_as_red += 1
        turns_total += game2.turn

    games = half * 2
    results = {
        "BLUE": wins_as_blue, "RED": wins_as_red,
        "DRAW": games - wins_as_blue - wins_as_red,
    }

    if outdir:
        merged = Recorder()
        for idx, rec in enumerate(records + swapped):
            for row in rec.turns:
                merged.turns.append(row)
            for row in rec.units:
                merged.units.append(row)
            for row in rec.combat:
                merged.combat.append(row)
        merged.write_csv(outdir)

    stats = analyse(records, "BLUE")
    rival = analyse(records, "RED")
    as_blue = wins_as_blue / half
    as_red = wins_as_red / half
    flags = find_anomalies(stats, as_blue, as_red, games, rival)

    lines = []
    add = lines.append
    add("=" * 78)
    add(f"FIELDBOX DIAGNOSTIC  preset={preset}  BLUE={blue_name}  RED={red_name}")
    add(f"{games} games over map seeds {list(map_seeds)}")
    add("=" * 78)
    add(f"{blue_name} wins {wins_as_blue}/{half} playing first (BLUE), "
        f"{wins_as_red}/{half} playing second (RED)")
    add(f"overall {wins_as_blue + wins_as_red}/{games}"
        f"   mean length {turns_total / max(games,1):.1f} turns")
    add("")
    add(f"{blue_name} force performance (games where it played BLUE)")
    add(f"  {'type':<6}{'n':>4}{'lost':>7}{'kills':>7}{'fires/turn':>12}"
        f"{'holdsObj':>10}{'cutoff':>8}{'onStartHex':>12}{'endStr':>8}"
        f"{'REDlost':>8}")
    for utype, st in sorted(stats.items()):
        if not st.count:
            continue
        opp = rival.get(utype)
        opp_rate = f"{opp.lost / opp.count:.0%}" if opp and opp.count else "-"
        add(f"  {utype:<6}{st.count:>4}{st.lost / st.count:>6.0%}"
            f"{st.kills:>7}"
            f"{st.engagements / max(st.unit_turns,1):>12.2f}"
            f"{st.objective_turns / max(st.unit_turns,1):>10.0%}"
            f"{st.cutoff_turns / max(st.unit_turns,1):>8.0%}"
            f"{st.stuck_turns / max(st.unit_turns,1):>12.0%}"
            f"{st.end_strength / max(st.end_count,1):>8.0f}{opp_rate:>8}")
    add("")
    add("Culmination: mean supply by columns advanced from own baseline")
    rows = culmination(records, "BLUE")
    if rows:
        add("  cols   " + "".join(f"{r[0]:>7}" for r in rows))
        add("  stock  " + "".join(f"{r[1]:>7.0f}" for r in rows))
        add("  n      " + "".join(f"{r[2]:>7}" for r in rows))
    add("")
    if flags:
        add(f"ANOMALIES ({len(flags)})")
        for f in flags:
            add(f"  ! {f}")
    else:
        add("ANOMALIES: none detected")
    add("=" * 78)
    return "\n".join(lines)
