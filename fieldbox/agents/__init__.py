"""Agent interface.

Every agent -- scripted, doctrinal, neural, RL -- implements the same `act`.
That uniformity is what makes the Elo ladder in arena.py meaningful.
"""

from __future__ import annotations

from typing import Dict, Type

from typing import List

from ..sim.orders import Order
from ..sim.units import Side


class Agent:
    name: str = "agent"

    def reset(self, side: Side, world) -> None:
        """Called once at match start with the agent's side and the static
        world (map, provinces). Never the Game."""
        self.side = side

    def act(self, obs) -> List[Order]:
        """Orders for this turn, most important first. Only `obs` (an
        Observation) may be read."""
        raise NotImplementedError


REGISTRY: Dict[str, Type[Agent]] = {}


def register(cls: Type[Agent]) -> Type[Agent]:
    REGISTRY[cls.name] = cls
    return cls


from .scripted import HoldAgent, RandomAgent, RushAgent  # noqa: E402,F401
from .doctrine import DoctrineAgent  # noqa: E402,F401

__all__ = [
    "Agent", "REGISTRY", "register",
    "RandomAgent", "HoldAgent", "RushAgent", "DoctrineAgent",
]
