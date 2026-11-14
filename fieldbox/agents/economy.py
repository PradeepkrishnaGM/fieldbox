"""Temporary economy behaviour for the scripted agents (M4 step 6).

A fixed build list and a simple FOB rule so that games exercise production,
supply sources and structures. The proper commander replaces this in M7.
"""

from __future__ import annotations

from typing import List, Optional

from .. import params as P
from ..core.hexgrid import Domain, distance, neighbors
from ..sim.observation import Observation, UnitView
from ..sim.orders import Attack, Bombard, Build, Construct, MoveTo, Order, Strike
from ..sim.structures import StructureType as ST
from ..sim.units import STATS, UnitType

BUILD_LIST = tuple(UnitType[n] for n in P.TEMP_BUILD_LIST)
AIR_LIST = tuple(UnitType[n] for n in P.TEMP_AIR_BUILD_LIST)


class FixedBuilder:
    """Round-robin over BUILD_LIST at land-producing structures and
    AIR_LIST at airfields."""

    def __init__(self) -> None:
        self.i = 0
        self.j = 0

    def builds(self, obs: Observation) -> List[Build]:
        out: List[Build] = []
        pp = obs.pp
        for st in obs.own_structures:
            dom = st.stats.produces
            if dom not in (Domain.LAND, Domain.AIR):
                continue
            if len(obs.queues.get(st.sid, ())) >= P.TEMP_QUEUE_DEPTH:
                continue
            land = dom is Domain.LAND
            lst, k = (BUILD_LIST, self.i) if land else (AIR_LIST, self.j)
            t = lst[k % len(lst)]
            cost = STATS[t].cost
            if pp < cost:
                break
            out.append(Build(st.sid, t))
            pp -= cost
            if land:
                self.i += 1
            else:
                self.j += 1
        return out


def _fob_site(obs: Observation):
    """The own-held capital nearest the enemy HQ that is far from our other bases."""
    side = obs.side
    bases = [s.coord for s in obs.own_structures if s.kind in (ST.HQ, ST.FOB)]
    bases += list(obs.building)
    enemy_hq = obs.hq[side.other]
    cands = [c for c in obs.objectives
             if obs.objective_holder(c) == side and obs.map.passable(c, Domain.LAND)
             and obs.structure_at(c) is None
             and all(distance(c, b) >= P.TEMP_FOB_SPACING for b in bases)]
    return min(cands, key=lambda c: (distance(c, enemy_hq), c), default=None)


def engineer_order(obs: Observation, unit: UnitView) -> Optional[Order]:
    """FOB building for a unit with `builds`. None = nothing to do."""
    if isinstance(unit.order, Construct):
        return unit.order
    n_fobs = len(obs.structures_of(obs.side, ST.FOB)) + len(obs.building)
    if n_fobs >= P.TEMP_MAX_FOBS or obs.pp < P.ECONOMY.fob_cost:
        return None
    site = _fob_site(obs)
    if site is None:
        return None
    if distance(unit.coord, site) > 1:
        return MoveTo(unit.uid, site)
    return Construct(unit.uid, site, ST.FOB)


def nearest_structure(obs: Observation, unit: UnitView, avoid=frozenset(),
                      undefended: bool = False):
    """Nearest enemy structure this unit could attack (HQ first on ties).
    `avoid` = hexes to skip (enemy SAM cover); `undefended` skips structures
    with an enemy land unit on the hex or next to it."""
    st = unit.stats
    if Domain.LAND not in st.targets:
        return None
    cands = []
    for s in obs.enemy_structures:
        if s.coord in avoid:
            continue
        if undefended and any(e.domain is Domain.LAND and distance(e.coord, s.coord) <= 1
                              for e in obs.enemy_units):
            continue
        cands.append(s)
    return min(cands, key=lambda s: (distance(unit.coord, s.coord), s.kind is not ST.HQ, s.sid),
               default=None)


def structure_order(obs: Observation, unit: UnitView, avoid=frozenset(),
                    undefended: bool = False) -> Optional[Order]:
    """Go for the nearest enemy structure. None if the unit cannot."""
    target = nearest_structure(obs, unit, avoid, undefended)
    if target is None:
        return None
    st = unit.stats
    if st.domain is Domain.AIR:
        return Strike(unit.uid, target.sid)
    if st.indirect:
        if distance(unit.coord, target.coord) <= st.reach:
            return Bombard(unit.uid, target.coord)
        if not obs.map.passable(target.coord, st.domain):
            return None                  # e.g. a ship cannot sail onto a port hex
        return MoveTo(unit.uid, target.coord)
    if any(e.coord == target.coord and e.domain is Domain.LAND for e in obs.enemy_units):
        return None
    return Attack(unit.uid, target.sid, structure=True)
