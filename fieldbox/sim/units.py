"""Unit types, capabilities, and NATO symbology.

The key design decision here is that unit behaviour is described by *data*
rather than by type checks. Adding a new unit means adding a row to `STATS`;
it should never mean adding an `if utype is ...` branch to the engine or the
agents. With nine unit types across three domains, a type-switch design turns
into a combinatorial mess almost immediately.

Three axes carry almost all the behaviour:

* `domain`   -- where the unit exists, which decides what terrain it can
                enter and what can shoot at it.
* `targets`  -- which domains it can engage. This is the rock/paper/scissors:
                air strikes ground, ground air-defence strikes air, and
                ground manoeuvre overruns air-defence.
* capability flags (`can_capture`, `entrenches`) -- what it may do with
                ground it stands on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, FrozenSet

from .. import params as P
from ..core.hexgrid import Domain, Terrain


class Side(IntEnum):
    BLUE = 0
    RED = 1

    @property
    def other(self) -> "Side":
        return Side.RED if self is Side.BLUE else Side.BLUE


# MIL-STD-2525C battle dimension, position 3 of the SIDC.
DIMENSION_CODE: Dict[Domain, str] = {
    Domain.LAND: "G",
    Domain.AIR: "A",
    Domain.SEA: "S",
}


class UnitType(IntEnum):
    INFANTRY = 0
    TANK = 1
    IFV = 2
    RECON = 3
    ARTILLERY = 4
    SAM = 5
    HELICOPTER = 6
    JET = 7
    SHIP = 8
    ENGINEER = 9          # added in M3; existing values unchanged


INDIRECT_COEF = P.COMBAT.indirect_coef     # area fire: attrition, not decision
DIRECT_AD_COEF = P.COMBAT.direct_ad_coef   # aimed missile engagement


@dataclass(frozen=True)
class UnitStats:
    move: float               # movement points per turn
    attack: float             # offensive combat coefficient
    defense: float            # defensive combat coefficient
    reach: int                # max engagement range (1 = direct fire only)
    spot: int                 # detection radius (used once fog of war lands)
    label: str
    glyph: str                # single char for the ASCII map -- must be unique
    sidc_function: str        # MIL-STD-2525C function ID, positions 5-10
    domain: Domain
    targets: FrozenSet[Domain]
    can_capture: bool         # may hold an objective and score for it
    entrenches: bool          # may dig in for a defensive bonus
    # Ranged fire effectiveness. Tube artillery is area attrition; a missile
    # engagement is aimed, so dedicated air defence does not take the penalty
    # -- with it, a SAM needed 6.7 turns to down a helicopter that killed a
    # gun in 2.2 attacks, so air defence could never protect anything.
    fire_coef: float = INDIRECT_COEF
    # Supply burned per turn just existing, and per attack. Aircraft burn
    # stock roughly an order of magnitude faster than infantry, which is what
    # forces the sortie cycle rather than letting them loiter forever.
    upkeep: float = P.UNIT_DEFAULT_UPKEEP
    combat_cost: float = P.UNIT_DEFAULT_COMBAT_COST
    cost: int = 0             # production points to build (M4)
    build_turns: int = 0      # turns in a production queue (M4)
    builds: bool = False      # engineer-like: may Construct structures and Repair them (M4)

    @property
    def indirect(self) -> bool:
        """Engages at range without suffering return fire."""
        return self.reach > 1


def _stats(name: str) -> UnitStats:
    d = dict(P.UNIT_STATS[name])
    d["domain"] = Domain[d["domain"]]
    d["targets"] = frozenset(Domain[t] for t in d["targets"])
    return UnitStats(**d)


# Stats come from fieldbox.params.UNIT_STATS (keyed by name).
STATS: Dict[UnitType, UnitStats] = {t: _stats(t.name) for t in UnitType}

# Terrain multipliers applied to the ATTACKER, by unit type. Close terrain
# punishes vehicles; infantry is comfortable everywhere.
ATTACK_TERRAIN_MOD: Dict[UnitType, Dict[Terrain, float]] = {
    UnitType[u]: {Terrain[t]: m for t, m in mods.items()}
    for u, mods in P.ATTACK_TERRAIN_MOD.items()
}

MAX_ENTRENCH = P.COMBAT.max_entrench
ENTRENCH_BONUS = P.COMBAT.entrench_bonus  # defensive multiplier per entrenchment level

# A defender that cannot engage the attacker's domain is not helpless -- it
# disperses, uses cover and puts up small-arms fire -- but it is at a serious
# disadvantage. Without this, air power takes literally no losses over ground.
NO_RETURN_FIRE_MULT = P.COMBAT.no_return_fire_mult


def can_engage(attacker_type: UnitType, defender_domain: Domain) -> bool:
    return defender_domain in STATS[attacker_type].targets


class UnitLike:
    """Read-only behaviour shared by live units and observation views.
    Subclasses provide uid, side, utype, q, r, strength, entrench, supply,
    in_supply."""

    @property
    def coord(self):
        return (self.q, self.r)

    @property
    def stats(self) -> UnitStats:
        return STATS[self.utype]

    @property
    def domain(self) -> Domain:
        return STATS[self.utype].domain

    @property
    def alive(self) -> bool:
        return self.strength > 0.0

    def can_engage(self, other: "UnitLike") -> bool:
        return other.domain in self.stats.targets


@dataclass
class Unit(UnitLike):
    uid: int
    side: Side
    utype: UnitType
    q: int
    r: int
    strength: float = 100.0
    moves_left: float = 0.0
    entrench: int = 0
    supply: float = 100.0
    in_supply: bool = True

    def clone(self) -> "Unit":
        return Unit(
            self.uid, self.side, self.utype, self.q, self.r,
            self.strength, self.moves_left, self.entrench,
            self.supply, self.in_supply,
        )

    def sidc(self) -> str:
        """MIL-STD-2525C symbol identification code (15 chars).

        Field layout, which is fiddly and easy to get wrong:
            1      coding scheme        S = warfighting
            2      affiliation          F = friend, H = hostile
            3      battle dimension     G ground / A air / S sea surface
            4      status               P = present
            5-10   function ID          e.g. UCI--- = infantry
            11     symbol modifier 1    HQ / task force / dummy
            12     symbol modifier 2    echelon  <-- E = company (ground only)
            13-14  country code
            15     order of battle

        Consumed directly by milsymbol.js in the rendering phase, so the
        engine never has to know anything about drawing.
        """
        st = self.stats
        affiliation = "F" if self.side is Side.BLUE else "H"
        dimension = DIMENSION_CODE[st.domain]
        # Echelon is a ground-unit concept; air and naval tracks leave it blank.
        echelon = "E" if st.domain is Domain.LAND else "-"
        code = f"S{affiliation}{dimension}P{st.sidc_function}-{echelon}"
        return code.ljust(15, "-")[:15]

    def __repr__(self) -> str:
        return (
            f"<{self.stats.label}#{self.uid} {self.side.name} "
            f"({self.q},{self.r}) str={self.strength:.0f} "
            f"sup={self.supply:.0f}{'' if self.in_supply else ' CUT'}>"
        )
