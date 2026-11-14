"""Fog of war (PLAN.md §3.8): what each side can see, and what it remembers.

Visibility is per domain, because radar sees air and sea units further than
land units: `Visibility.land` is where land units (and structures) can be
seen, `Visibility.air` where air and sea units can be seen. Unit and
structure discs count for both.

Memory is per side: enemy units that leave sight become ghosts (last
position, type, strength, age) for `ghost_ttl` turns; enemy structures stay
known, with their last seen strength, until seen destroyed or captured.
The enemy HQ position is public (the map is point-symmetric, so it is
deducible); everything else has to be scouted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List

from .. import params as P
from ..core.hexgrid import Coord, Domain, disc_offsets
from .structures import StructureType as ST

FOG = P.FOG


def radar_range(base: int, strength: float, power: float, has_data_center: bool) -> int:
    """Effective radar radius in hexes (floored)."""
    r = base * (strength / 100.0) * power * (1.0 if has_data_center else 0.5)
    return int(r)


@dataclass(frozen=True)
class Visibility:
    land: FrozenSet[Coord]
    air: FrozenSet[Coord]          # air and sea

    def sees(self, domain: Domain, coord: Coord) -> bool:
        return coord in (self.land if domain is Domain.LAND else self.air)


def compute_visibility(game, side) -> Visibility:
    in_bounds = game.map.in_bounds
    both = set()
    for u in game.units_of(side):
        uq, ur = u.coord
        for dq, dr in disc_offsets(u.stats.spot):
            both.add((uq + dq, ur + dr))
    structs = game.structures_of(side)
    has_dc = any(s.kind is ST.DATACENTER and s.strength > 0 for s in structs)
    land, air = set(both), set(both)
    for s in structs:
        sq, sr = s.coord
        for dq, dr in disc_offsets(FOG.structure_spot):
            c = (sq + dq, sr + dr)
            land.add(c)
            air.add(c)
        if s.kind is ST.RADAR:
            pf = game.power[side]
            for base, target in ((FOG.radar_range_ground, land), (FOG.radar_range_air_sea, air)):
                for dq, dr in disc_offsets(radar_range(base, s.strength, pf, has_dc)):
                    target.add((sq + dq, sr + dr))
    return Visibility(frozenset(c for c in land if in_bounds(c)),
                      frozenset(c for c in air if in_bounds(c)))


@dataclass(frozen=True)
class GhostRecord:
    uid: int
    side: int
    utype: object          # UnitType
    q: int
    r: int
    strength: float
    seen: int              # turn it was last seen

    @property
    def domain(self) -> Domain:
        from .units import STATS
        return STATS[self.utype].domain


@dataclass(frozen=True)
class StructureRecord:
    sid: int
    kind: object           # StructureType
    side: int
    q: int
    r: int
    strength: float
    seen: int


class FogMemory:
    """One side's memory of the enemy."""

    def __init__(self) -> None:
        self.units: Dict[int, GhostRecord] = {}
        self.structures: Dict[int, StructureRecord] = {}

    def update(self, game, side, vis: Visibility) -> None:
        turn = game.turn
        enemy = side.other
        for u in game.units_of(enemy):
            if vis.sees(u.domain, u.coord):
                self.units[u.uid] = GhostRecord(u.uid, int(u.side), u.utype, u.q, u.r,
                                                u.strength, turn)
        for uid in sorted(self.units):
            rec = self.units[uid]
            if rec.seen == turn:
                continue
            # We can see the spot and it is empty, or the contact is stale.
            if vis.sees(rec.domain, (rec.q, rec.r)) or turn - rec.seen > FOG.ghost_ttl:
                del self.units[uid]
        for s in game.structures_of(enemy):
            if s.coord in vis.land:
                self.structures[s.sid] = StructureRecord(s.sid, s.kind, int(s.side), s.q, s.r,
                                                         s.strength, turn)
        for sid in sorted(self.structures):
            rec = self.structures[sid]
            if rec.seen == turn:
                continue
            now = game.structures.get(sid)
            if (now is None or now.side is side) and (rec.q, rec.r) in vis.land:
                del self.structures[sid]       # seen destroyed or captured

    def ghosts(self, turn: int) -> List[GhostRecord]:
        """Contacts not currently visible, oldest last, in uid order."""
        return [r for _, r in sorted(self.units.items()) if r.seen < turn]


def initial_memory(game, side) -> FogMemory:
    """The enemy HQ position is public knowledge from the start (never seen: age turn+1)."""
    mem = FogMemory()
    for s in game.structures_of(side.other, ST.HQ):
        mem.structures[s.sid] = StructureRecord(s.sid, s.kind, int(s.side), s.q, s.r, s.strength, -1)
    return mem
