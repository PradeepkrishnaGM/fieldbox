"""Shared test helpers.

Slow tests (more than about 2 s) are decorated with ``@slow`` and run only
when ``FIELDBOX_SLOW=1`` is set:

    python -m unittest discover -s tests                   # quick
    FIELDBOX_SLOW=1 python -m unittest discover -s tests   # full
"""

import os
import unittest

slow = unittest.skipUnless(os.environ.get("FIELDBOX_SLOW") == "1", "slow")


def make_game(units, world_seed=1, preset="tiny", seed=0, max_turns=None):
    """A Game on a generated map with the given units only.

    `units` is a list of (side, UnitType, coord). Hand-picked coordinates must
    be passable for the unit's domain; `land_near` finds some.
    """
    from fieldbox.sim.engine import Game
    from fieldbox.sim.units import Unit
    from fieldbox.world import generate_world

    game = Game(generate_world(world_seed, preset), seed=seed, max_turns=max_turns)
    game.units.clear()
    game._by_coord.clear()
    for i, (side, utype, coord) in enumerate(units):
        u = Unit(i, side, utype, coord[0], coord[1])
        game.units[i] = u
        game._by_coord.setdefault(u.coord, {})[u.domain] = u
    for s in game.supply_net:
        game._update_supply(s, emit=False)
    return game


def land_line(game, start, n):
    """n passable land hexes in a row going east from the first land hex at or after `start`."""
    from fieldbox.core.hexgrid import Domain
    q, r = start
    out = []
    while len(out) < n:
        if game.map.passable((q, r), Domain.LAND):
            out.append((q, r))
        elif out:
            break
        q += 1
    return out
