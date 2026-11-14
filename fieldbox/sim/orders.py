"""Orders: Hold, MoveTo, Attack, Bombard, Strike, Build,
Construct, Repair.

Orders are persistent: a unit keeps its last order until it completes, it
becomes illegal, or the unit gets a new one. "No new order" is not Hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from ..core.hexgrid import Coord
from .structures import StructureType
from .units import UnitType


@dataclass(frozen=True)
class Hold:
    """Stay put; dig in if the unit can. Persists until replaced."""
    uid: int


@dataclass(frozen=True)
class MoveTo:
    """Path toward `dest`. Persists until reached (then becomes Hold)."""
    uid: int
    dest: Coord


@dataclass(frozen=True)
class Attack:
    """Close with `target` and assault it. Persists until the target dies.
    With `structure=True`, `target` is a structure id (legal only while no
    enemy land unit stands on its hex)."""
    uid: int
    target: int
    structure: bool = False


@dataclass(frozen=True)
class Bombard:
    """Ranged fire at the enemy on `dest`, if within reach. No return fire.
    Persists while `dest` holds an engageable enemy."""
    uid: int
    dest: Coord


@dataclass(frozen=True)
class Strike:
    """An air unit flies to structure `sid` and attacks it. Persistent."""
    uid: int
    sid: int


@dataclass(frozen=True)
class Build:
    """Queue `utype` at producing structure `sid` (HQ/industry: land,
    airfield: air, port: sea). Costs PP up front, no CP. Not a unit order."""
    sid: int
    utype: UnitType


@dataclass(frozen=True)
class Construct:
    """An engineer builds a structure (FOB) on its hex or an adjacent hex
    over `fob_build_turns`. Costs PP up front. Persistent until done."""
    uid: int
    coord: Coord
    kind: StructureType = StructureType.FOB


@dataclass(frozen=True)
class Repair:
    """An engineer repairs a friendly structure on or next to its hex."""
    uid: int
    sid: int


Order = Union[Hold, MoveTo, Attack, Bombard, Strike, Build, Construct, Repair]
ORDER_TYPES = (Hold, MoveTo, Attack, Bombard, Strike, Build, Construct, Repair)
