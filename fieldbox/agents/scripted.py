"""Scripted baselines. These are the floor your learned agents must clear.

All three are domain-aware: a unit is never ordered to attack something it
cannot engage, and indirect-fire units bombard rather than closing.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from .. import params as P
from ..sim.engine import Attack, Bombard, Game, Hold, MoveTo, Order
from ..core.hexgrid import distance
from ..sim.units import Side, Unit
from . import Agent, register

COUNTERATTACK_THRESHOLD = P.COUNTERATTACK_THRESHOLD  # only counterattack an enemy this broken


def engageable(game: Game, unit: Unit, side: Side) -> List[Unit]:
    return [e for e in game.units_of(side.other) if unit.can_engage(e)]


def simple_engage(game: Game, unit: Unit, target: Unit) -> Order:
    """Bombard if we shoot at range, otherwise close and attack."""
    if unit.stats.indirect:
        if distance(unit.coord, target.coord) <= unit.stats.reach:
            return Bombard(unit.uid, target.coord)
        return MoveTo(unit.uid, target.coord)
    return Attack(unit.uid, target.uid)


@register
class RandomAgent(Agent):
    """Uniform over legal orders. The absolute performance floor."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)

    def act(self, game: Game, side: Side) -> Dict[int, Order]:
        return {
            u.uid: self.rng.choice(game.legal_orders(u))
            for u in game.units_of(side)
        }


@register
class HoldAgent(Agent):
    """Mass on the objectives and entrench.

    Every unit is assigned to its *nearest* objective, several units to an
    objective if that is how the distances fall. The first version handed out
    one objective per unit and then ordered everybody else to Hold where they
    stood -- with three objectives and eight units, five of them sat on the
    deployment line for the whole game, which is why a dug-in defender was
    losing to random play.

    Units that cannot enter an already-occupied objective hex stop adjacent
    to it, so the assignment naturally forms a perimeter around the ground
    rather than a queue for one hex.
    """

    name = "hold"

    def act(self, game: Game, side: Side) -> Dict[int, Order]:
        orders: Dict[int, Order] = {}
        objectives = game.objectives
        if not objectives:
            return {u.uid: Hold(u.uid) for u in game.units_of(side)}

        for u in game.units_of(side):
            targets = engageable(game, u, side)
            adjacent = [
                e for e in game.enemies_adjacent(u.coord, side)
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

            goal = _nearest_objective(game, u)
            if goal is None or u.coord == goal or adjacent:
                orders[u.uid] = Hold(u.uid)
            else:
                orders[u.uid] = MoveTo(u.uid, goal)
        return orders


@register
class RushAgent(Agent):
    """Everything at the nearest engageable enemy. Wins against passive
    opponents, loses to anything that concentrates fire."""

    name = "rush"

    def act(self, game: Game, side: Side) -> Dict[int, Order]:
        orders: Dict[int, Order] = {}
        for u in game.units_of(side):
            targets = engageable(game, u, side)
            if not targets:
                orders[u.uid] = Hold(u.uid)
                continue
            nearest = min(targets, key=lambda e: (distance(u.coord, e.coord), e.uid))
            orders[u.uid] = simple_engage(game, u, nearest)
        return orders


def _nearest_objective(game: Game, unit: Unit) -> Optional[tuple]:
    """Closest objective this unit's domain can actually reach."""
    reachable = [
        c for c in game.objectives
        if game.map.passable(c, unit.domain)
    ]
    if not reachable:
        return None
    return min(
        reachable,
        key=lambda c: (distance(unit.coord, c), -game.objectives[c], c),
    )
