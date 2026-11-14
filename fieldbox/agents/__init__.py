"""Agent interface.

Every agent -- scripted, doctrinal, neural, RL -- implements the same `act`.
That uniformity is what makes the Elo ladder in arena.py meaningful.
"""

from __future__ import annotations

from typing import Dict, Type

from ..sim.engine import Game, Order
from ..sim.units import Side


class Agent:
    name: str = "agent"

    def reset(self, game: Game, side: Side) -> None:
        """Called once at match start."""

    def act(self, game: Game, side: Side) -> Dict[int, Order]:
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
