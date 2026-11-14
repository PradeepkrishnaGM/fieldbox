"""Scripted baselines. These are the floor your learned agents must clear.

All three are domain-aware: a unit is never ordered to attack something it
cannot engage, and indirect-fire units bombard rather than closing.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from .. import params as P
from ..sim.observation import Observation, UnitView
from ..sim.orders import Attack, Bombard, Hold, MoveTo, Order
from ..core.hexgrid import distance
from ..sim.units import Side
Unit = UnitView
from . import Agent, register
from .economy import FixedBuilder, engineer_order, nearest_structure, structure_order

COUNTERATTACK_THRESHOLD = P.COUNTERATTACK_THRESHOLD  # only counterattack an enemy this broken


def engageable(obs: Observation, unit: Unit, side: Side) -> List[Unit]:
    return [e for e in obs.units_of(side.other) if unit.can_engage(e)]


def simple_engage(obs: Observation, unit: Unit, target: Unit) -> Order:
    """Bombard if we shoot at range, otherwise close and attack."""
    if unit.stats.indirect:
        if distance(unit.coord, target.coord) <= unit.stats.reach:
            return Bombard(unit.uid, target.coord)
        if not obs.map.passable(target.coord, unit.domain):
            return Hold(unit.uid)         # e.g. artillery cannot sail onto a ship
        return MoveTo(unit.uid, target.coord)
    return Attack(unit.uid, target.uid)


@register
class RandomAgent(Agent):
    """Uniform over legal orders. The absolute performance floor."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)

    def act(self, obs: Observation) -> List[Order]:
        return [self.rng.choice(obs.legal_orders(u)) for u in obs.units_of(obs.side)]


@register
class HoldAgent(Agent):
    """Mass on the objectives and entrench.

    Every unit is assigned to its *nearest* objective, several units to an
    objective if that is how the distances fall. The first version handed out
    one objective per unit and then ordered everybody else to Hold where they
    stood -- with three objectives and eight units, five of them sat on the
    deployment line for the whole obs, which is why a dug-in defender was
    losing to random play.

    Units that cannot enter an already-occupied objective hex stop adjacent
    to it, so the assignment naturally forms a perimeter around the ground
    rather than a queue for one hex.
    """

    name = "hold"

    def act(self, obs: Observation) -> List[Order]:
        side = obs.side
        orders: Dict[int, Order] = {}
        objectives = obs.objectives
        if not objectives:
            return [Hold(u.uid) for u in obs.units_of(side)]

        for u in obs.units_of(side):
            targets = engageable(obs, u, side)
            adjacent = [
                e for e in obs.enemies_adjacent(u.coord, side)
                if u.can_engage(e)
            ]

            # Ranged units shoot from where they are rather than closing.
            if u.stats.indirect and targets:
                in_range = [
                    e for e in targets
                    if distance(u.coord, e.coord) <= u.stats.reach
                ]
                if in_range:
                    best = min(in_range, key=lambda e: (e.strength, e.uid))
                    orders[u.uid] = Bombard(u.uid, best.coord)
                    continue

            if adjacent and not u.stats.indirect:
                weakest = min(adjacent, key=lambda e: (e.strength, e.uid))
                # Counterattack only to finish something already broken.
                # Attacking out of an entrenchment resets `entrench` to 0 and
                # inflicts attacker losses, which throws away the terrain and
                # entrenchment multipliers that are the whole point of
                # defending. Attacking on contact scored 10/30 against random;
                # counterattacking only below the threshold scores 30/30.
                if weakest.strength < COUNTERATTACK_THRESHOLD:
                    orders[u.uid] = Attack(u.uid, weakest.uid)
                else:
                    orders[u.uid] = Hold(u.uid)
                continue

            goal = _nearest_objective(obs, u)
            if goal is None or u.coord == goal or adjacent:
                orders[u.uid] = Hold(u.uid)
            else:
                orders[u.uid] = MoveTo(u.uid, goal)
        return [orders[k] for k in sorted(orders)]


@register
class RushAgent(Agent):
    """Everything at the nearest engageable enemy. Wins against passive
    opponents, loses to anything that concentrates fire."""

    name = "rush"

    def __init__(self) -> None:
        self.builder = FixedBuilder()

    def reset(self, side, world) -> None:
        super().reset(side, world)
        self.builder = FixedBuilder()

    def act(self, obs: Observation) -> List[Order]:
        side = obs.side
        orders: Dict[int, Order] = {}
        for u in obs.units_of(side):
            if u.stats.builds:
                eng = engineer_order(obs, u)
                if eng is not None:
                    orders[u.uid] = eng
                    continue
            targets = engageable(obs, u, side)
            nearest = min(targets, key=lambda e: (distance(u.coord, e.coord), e.uid),
                          default=None)
            struct = nearest_structure(obs, u)
            # Structures count as enemies; ties go to the unit.
            if struct is not None and (nearest is None or
                                       distance(u.coord, struct.coord) < distance(u.coord, nearest.coord)):
                orders[u.uid] = structure_order(obs, u) or (
                    simple_engage(obs, u, nearest) if nearest else self._search(obs, u))
            elif nearest is not None:
                orders[u.uid] = simple_engage(obs, u, nearest)
            else:
                orders[u.uid] = self._search(obs, u)
        return [orders[k] for k in sorted(orders)] + self.builder.builds(obs)

    @staticmethod
    def _search(obs: Observation, u: Unit) -> Order:
        """Nothing visible or worth a structure strike: head for the nearest lost contact."""
        ghosts = [g for g in obs.ghosts if u.can_engage(g) and obs.map.passable(g.coord, u.domain)]
        if not ghosts:
            return Hold(u.uid)
        g = min(ghosts, key=lambda g: (distance(u.coord, g.coord), g.uid))
        return MoveTo(u.uid, g.coord)


def _nearest_objective(obs: Observation, unit: Unit) -> Optional[tuple]:
    """Closest objective this unit's domain can actually reach."""
    reachable = [
        c for c in obs.objectives
        if obs.map.passable(c, unit.domain)
    ]
    if not reachable:
        return None
    return min(
        reachable,
        key=lambda c: (distance(unit.coord, c), -obs.objectives[c], c),
    )
