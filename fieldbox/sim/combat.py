"""Combat maths (PLAN.md §3.9): pure functions, milsim's numbers unchanged.

Shared by the engine (to resolve fights) and the Observation (so agents can
estimate odds with exactly the same formula).
"""

from __future__ import annotations

from .. import params as P
from ..core.hexgrid import Domain, HexMap
from .supply import supply_factor
from .units import (ATTACK_TERRAIN_MOD, ENTRENCH_BONUS, NO_RETURN_FIRE_MULT,
                    UnitLike)

BASE_LOSS = P.COMBAT.base_loss
MIN_LOSS = P.COMBAT.min_loss
MAX_LOSS = P.COMBAT.max_loss
FLANK_BONUS = P.COMBAT.flank_bonus
RIVER_ATTACK_MULT = P.COMBAT.river_attack_mult


def clamp_loss(x: float) -> float:
    return max(MIN_LOSS, min(MAX_LOSS, x))


def attack_power(attacker: UnitLike, defender: UnitLike, hexmap: HexMap,
                 supporting: int) -> float:
    """Assault power. `supporting` = other friendlies adjacent to the
    defender that can engage it (the flank bonus)."""
    st = attacker.stats
    terrain = hexmap.get(defender.coord)
    mod = ATTACK_TERRAIN_MOD[attacker.utype].get(terrain, 1.0)
    power = (attacker.strength / 100.0) * st.attack * mod
    power *= supply_factor(attacker.supply)
    # Land assaults into a river hex are penalised unless there is a bridge.
    c = defender.coord
    if st.domain is Domain.LAND and c in hexmap.rivers and c not in hexmap.roads:
        power *= RIVER_ATTACK_MULT
    return power * (1.0 + FLANK_BONUS * supporting)


def fire_power(attacker: UnitLike) -> float:
    """Ranged (indirect or missile) fire: no terrain or supply modifiers,
    as in milsim."""
    st = attacker.stats
    return (attacker.strength / 100.0) * st.attack * st.fire_coef


def defense_power(defender: UnitLike, hexmap: HexMap, attacker: UnitLike = None) -> float:
    st = defender.stats
    terr = hexmap.def_mult(defender.coord, st.domain)
    ent = 1.0 + ENTRENCH_BONUS * defender.entrench
    power = (defender.strength / 100.0) * st.defense * terr * ent
    # Defence suffers less than attack from short supply.
    power *= 0.5 * (1.0 + supply_factor(defender.supply))
    if attacker is not None and not defender.can_engage(attacker):
        power *= NO_RETURN_FIRE_MULT
    return max(power, 1e-6)


def assault_losses(odds: float, jitter_d: float, jitter_a: float):
    """(defender_loss, attacker_loss)."""
    return (clamp_loss(BASE_LOSS * (odds ** 0.70) * jitter_d),
            clamp_loss(BASE_LOSS * (odds ** -0.50) * jitter_a))


def fire_loss(odds: float, jitter: float) -> float:
    return clamp_loss(BASE_LOSS * (odds ** 0.70) * jitter)
