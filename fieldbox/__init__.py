"""Fieldbox -- a deterministic hex wargame where AI commanders adapt.

Headless, stdlib-only engine: nine unit types across land, sea and air,
logistics, scripted and doctrinal agents, an evaluation harness with Elo
and CSV telemetry.
"""

__version__ = "0.1.0"

from .sim.engine import Attack, Bombard, Game, Hold, MoveTo  # noqa: E402
from .core.hexgrid import Domain, HexMap, Terrain, distance  # noqa: E402
from .sim.scenario import PRESETS, Scenario  # noqa: E402
from .sim.units import Side, Unit, UnitType  # noqa: E402

__all__ = [
    "Game", "Hold", "MoveTo", "Attack", "Bombard",
    "HexMap", "Terrain", "Domain", "distance",
    "Scenario", "PRESETS", "Side", "Unit", "UnitType",
]
