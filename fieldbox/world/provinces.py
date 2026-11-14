"""Provinces and regions: the RISK layer (PLAN.md §3.1)."""

from __future__ import annotations

import heapq
from typing import Dict, List, Tuple

from .. import params as P
from ..core.hexgrid import (Coord, Terrain, col_of, distance, mirror_coord,
                            neighbors)
from ..sim.units import Side
from .generate import _land_cost
from .worldmap import Province, Region

UNREACHABLE = 1.0e6   # distance base for land the seed cannot walk to


def _dijkstra(terrain, rivers, src: Coord) -> Dict[Coord, float]:
    dist = {src: 0.0}
    heap = [(0.0, src)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist[cur]:
            continue
        for n in neighbors(*cur):
            if n in terrain and terrain[n] is not Terrain.WATER:
                nd = d + _land_cost(terrain, rivers, n)
                if nd < dist.get(n, 1e18):
                    dist[n] = nd
                    heapq.heappush(heap, (nd, n))
    return dist


def build_provinces(wp: P.WorldParams, terrain, rivers, cities):
    W, H = wp.width, wp.height
    n = wp.provinces_per_half
    mirror = lambda c: mirror_coord(c, W, H)  # noqa: E731
    half = lambda c: Side.BLUE if col_of(c) < W // 2 else Side.RED  # noqa: E731

    # Seeds: city centres first, then farthest-point sampling over BLUE land.
    seeds: List[Coord] = [city[0] for city in cities[:len(cities) // 2]][:n]
    blue_land = sorted(c for c in terrain
                       if terrain[c] is not Terrain.WATER and col_of(c) < W // 2)
    while len(seeds) < n and blue_land:
        taken = seeds + [mirror(s) for s in seeds]
        seeds.append(max(blue_land, key=lambda c: (min(distance(c, s) for s in taken),
                                                   -c[1], -c[0])))
    seeds = seeds + [mirror(s) for s in seeds]     # seed i pairs with i + n
    seed_side = [Side.BLUE] * len(seeds[:len(seeds) // 2]) + [Side.RED] * (len(seeds) // 2)

    # Distance from every seed to every land hex (exact, so mirror ties match).
    dmaps = [_dijkstra(terrain, rivers, s) for s in seeds]
    land = [c for c in terrain if terrain[c] is not Terrain.WATER]

    province_of: Dict[Coord, int] = {}
    for c in land:
        def key(i: int):
            d = dmaps[i].get(c)
            d = UNREACHABLE + distance(seeds[i], c) if d is None else d
            # Tie-break: seed in the hex's own half first, then lower index.
            return (d, 0 if seed_side[i] is half(c) else 1, i)
        province_of[c] = min(range(len(seeds)), key=key)

    members: Dict[int, List[Coord]] = {i: [] for i in range(len(seeds))}
    for c in sorted(province_of):
        members[province_of[c]].append(c)

    city_centre = {city[0] for city in cities}
    nbr: Dict[int, set] = {i: set() for i in range(len(seeds))}
    for c, p in province_of.items():
        for m in neighbors(*c):
            q = province_of.get(m)
            if q is not None and q != p:
                nbr[p].add(q)

    provinces: List[Province] = []
    for i, s in enumerate(seeds):
        hexes = tuple(members[i])
        city = [c for c in hexes if c in city_centre]
        has_city = bool(city)
        if has_city:
            capital = city[0]
        elif i >= n:    # RED: the mirror of its BLUE pair, so ties cannot break symmetry
            capital = mirror(provinces[i - n].capital)
        else:       # cheapest hex within one step of the seed
            near = [c for c in hexes if distance(c, s) <= 1] or list(hexes)
            capital = min(near, key=lambda c: (_land_cost(terrain, rivers, c),
                                               distance(c, s), c))
        provinces.append(Province(
            id=i, seed=s, hexes=hexes, capital=capital, has_city=has_city,
            value=wp.province_base_value + (wp.city_value if has_city else 0),
            neighbours=tuple(sorted(nbr[i])), side=seed_side[i]))

    regions, region_of = _build_regions(wp, provinces)
    return tuple(provinces), province_of, regions, region_of


def _build_regions(wp: P.WorldParams, provinces: List[Province]):
    """Grow regions of `region_size` adjacent provinces in BLUE's half, from
    the westmost free province; mirror each region. Leftovers become smaller
    regions, and a lone leftover joins a neighbouring region when that keeps
    it within region_size + 1."""
    n = wp.provinces_per_half
    col = lambda i: col_of(provinces[i].seed)  # noqa: E731
    free = set(range(n))
    groups: List[List[int]] = []
    while free:
        start = min(free, key=lambda i: (col(i), i))
        grp = [start]
        free.discard(start)
        while len(grp) < wp.region_size:
            cands = sorted({q for p in grp for q in provinces[p].neighbours
                            if q in free}, key=lambda q: (col(q), q))
            if not cands:
                break
            grp.append(cands[0])
            free.discard(cands[0])
        groups.append(grp)
    for g in [g for g in groups if len(g) == 1]:
        for other in groups:
            if other is not g and len(other) <= wp.region_size and any(
                    q in provinces[g[0]].neighbours for q in other):
                other.extend(g)
                groups.remove(g)
                break
    regions: List[Region] = []
    region_of: Dict[int, int] = {}
    for grp in groups:
        for members in (grp, [p + n for p in grp]):
            rid = len(regions)
            regions.append(Region(rid, tuple(sorted(members)), wp.region_bonus))
            for p in members:
                region_of[p] = rid
    return tuple(regions), region_of
