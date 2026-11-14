"""Structures (PLAN.md §3.3): fixed installations that produce, supply and score.

A structure sits on one hex, belongs to a side, has strength 0-100 and never
attacks. It does not use a stacking slot. Stats live in `params.STRUCTURE_STATS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Optional

from .. import params as P
from ..core.hexgrid import Coord, Domain


class StructureType(IntEnum):
    HQ = 0
    INDUSTRY = 1
    POWER = 2
    DATACENTER = 3
    RADAR = 4
    AIRFIELD = 5
    PORT = 6
    FOB = 7


@dataclass(frozen=True)
class StructureStats:
    defense: float
    capturable: bool
    power_demand: int
    produces: Optional[Domain]
    label: str


STATS: Dict[StructureType, StructureStats] = {}
for _t in StructureType:
    _d = P.STRUCTURE_STATS[_t.name]
    STATS[_t] = StructureStats(_d["defense"], _d["capturable"], _d["power_demand"],
                               Domain[_d["produces"]] if _d["produces"] else None, _d["label"])

STRUCTURE_VP: Dict[StructureType, int] = {
    StructureType[k]: v for k, v in P.ECONOMY.structure_vp}


class StructureLike:
    """Shared read-only properties for `Structure` and its observation view."""
    @property
    def coord(self) -> Coord:
        return (self.q, self.r)

    @property
    def stats(self) -> StructureStats:
        return STATS[self.kind]

    @property
    def alive(self) -> bool:
        return self.strength > 0.0


@dataclass
class Structure(StructureLike):
    sid: int
    kind: StructureType
    side: object                    # Side
    q: int
    r: int
    strength: float = 100.0

    def __repr__(self) -> str:
        return f"<{self.kind.name}#{self.sid} {self.side.name} ({self.q},{self.r}) str={self.strength:.0f}>"
