"""Orders (PLAN.md §3.4), M3 subset: Hold, MoveTo, Attack, Bombard.

Orders are persistent: a unit keeps its last order until it completes, it
becomes illegal, or the unit gets a new one. "No new order" is not Hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from ..core.hexgrid import Coord


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
    """Close with `target` and assault it. Persists until the target dies."""
    uid: int
    target: int


@dataclass(frozen=True)
class Bombard:
    """Ranged fire at the enemy on `dest`, if within reach. No return fire.
    Persists while `dest` holds an engageable enemy."""
    uid: int
    dest: Coord


Order = Union[Hold, MoveTo, Attack, Bombard]
ORDER_TYPES = (Hold, MoveTo, Attack, Bombard)
