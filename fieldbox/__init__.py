"""Fieldbox -- a deterministic hex wargame where AI commanders adapt.

Headless, stdlib-only engine: nine unit types across land, sea and air,
logistics, scripted and doctrinal agents, an evaluation harness with Elo
and CSV telemetry.
"""

__version__ = "0.1.0"

from .core.hexgrid import Domain, HexMap, Terrain, distance  # noqa: E402
from .params import PRESETS  # noqa: E402
from .sim.engine import Game  # noqa: E402
from .sim.observation import Observation  # noqa: E402
from .sim.orders import Attack, Bombard, Hold, MoveTo  # noqa: E402
from .sim.units import Side, Unit, UnitType  # noqa: E402
from .world import WorldMap, generate_world  # noqa: E402

__all__ = [
    "Game", "Observation", "Hold", "MoveTo", "Attack", "Bombard",
    "HexMap", "Terrain", "Domain", "distance",
    "PRESETS", "Side", "Unit", "UnitType", "WorldMap", "generate_world",
]
