"""Logistics.

One mechanism, three emergent behaviours -- which is why it is worth having
rather than bolting on three separate systems:

* **Culmination.** An advance that outruns its depots runs down its stocks and
  loses combat power, so offensives stall at a distance rather than rolling on
  forever. This is the single most under-modelled thing in most wargames.
* **Encirclement.** Supply is traced *through* hexes, and enemy zones of
  control block the trace. Cutting a corridor starves everything beyond it,
  so manoeuvre against the rear becomes worth doing.
* **Air sortie cycles.** Aircraft burn stock far faster than ground troops
  and can only replenish over friendly rear areas, so they naturally fly out,
  strike, and return to rearm instead of loitering over the objective forever.

The network is traced once per side per domain per turn (a Dijkstra over at
most a few hundred hexes), never per unit -- per-unit tracing is what makes
supply models slow.
"""

from __future__ import annotations

import heapq
from typing import Dict, List, Optional, Set

from .. import params as P
from ..core.hexgrid import Coord, Domain, HexMap, neighbors

# --- tuning (values live in fieldbox.params.SUPPLY) -------------------------

SUPPLY_MAX = P.SUPPLY.supply_max

HQ_BUDGET = P.SUPPLY.hq_supply_budget
FOB_BUDGET = P.SUPPLY.fob_supply_budget
PORT_BUDGET = P.SUPPLY.port_sea_budget
AIR_REARM_RADIUS = P.SUPPLY.air_rearm_radius

RESUPPLY_RATE = P.SUPPLY.resupply_rate
DRAIN_RATE = P.SUPPLY.drain_rate
STARVATION_LOSS = P.SUPPLY.starvation_loss
REPAIR_RATE = P.SUPPLY.repair_rate
REPAIR_CAP = P.SUPPLY.repair_cap

# Combat effectiveness as a function of stock. Above EFFECTIVE there is no
# penalty at all; at zero a unit fights at FLOOR of its rated power.
SUPPLY_EFFECTIVE = P.SUPPLY.supply_effective
SUPPLY_FLOOR = P.SUPPLY.supply_floor


def supply_factor(stock: float) -> float:
    """Multiplier on combat power for a unit holding `stock` supply."""
    if stock >= SUPPLY_EFFECTIVE:
        return 1.0
    ratio = max(0.0, stock) / SUPPLY_EFFECTIVE
    return SUPPLY_FLOOR + (1.0 - SUPPLY_FLOOR) * ratio


# --- network tracing ------------------------------------------------------


def depot_hexes(hex_map: HexMap, home_col: int, domain: Domain) -> List[Coord]:
    """Supply originates from the owning side's baseline."""
    out = []
    for row in range(hex_map.height):
        coord = (home_col - row // 2, row)
        if hex_map.in_bounds(coord) and hex_map.passable(coord, domain):
            out.append(coord)
    return out


def trace_network(
    hex_map: HexMap,
    sources: List[Coord],
    domain: Domain,
    blocked: Optional[Set[Coord]] = None,
    budget: float = 0.0,
) -> Set[Coord]:
    """Hexes reachable from `sources` within a movement-cost budget.

    `blocked` holds interdicted hexes -- supply may not be traced *through*
    them. Sources themselves are always in supply, so a depot cannot be
    starved by a unit parked next to it.
    """
    blocked = blocked or set()
    adj = hex_map.adjacency(domain)
    best: Dict[Coord, float] = {}
    frontier: List[tuple] = []
    push = heapq.heappush
    pop = heapq.heappop
    for src in sources:
        if src in adj:
            best[src] = 0.0
            push(frontier, (0.0, src))

    while frontier:
        cost, current = pop(frontier)
        if cost > best.get(current, 1e18):
            continue
        if cost >= budget:
            continue
        for nxt, step in adj.get(current, ()):
            if nxt in blocked:
                continue
            new_cost = cost + step
            if new_cost <= budget and new_cost < best.get(nxt, 1e18):
                best[nxt] = new_cost
                push(frontier, (new_cost, nxt))
    return set(best)


def interdicted(game, side) -> Set[Coord]:
    """Hexes enemy ground troops deny to a supply trace.

    A hex adjacent to enemy ground is interdicted unless a friendly ground
    unit is standing on it holding the corridor open. This is what makes
    cutting a supply line a real operation rather than a flavour text.
    """
    out: Set[Coord] = set()
    for unit in game.units.values():
        if not unit.alive or unit.side == side or unit.domain is not Domain.LAND:
            continue
        out.add(unit.coord)
        for n in neighbors(*unit.coord):
            out.add(n)
    for unit in game.units.values():
        if unit.alive and unit.side == side and unit.domain is Domain.LAND:
            out.discard(unit.coord)
    return out
