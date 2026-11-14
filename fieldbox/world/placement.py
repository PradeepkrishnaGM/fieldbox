"""Home provinces, HQs, starting structures and forces.

Everything is chosen for BLUE inside BLUE's half, then mirrored for RED, so
the start is fair by construction.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..core.hexgrid import (Coord, Terrain, col_of, connected_components,
                            distance, neighbors)
from ..sim.units import Side
from .worldmap import PlacedStructure, PlacedUnit, WorldMap

AIR_UNITS = ("HELICOPTER", "JET")
SEA_UNITS = ("SHIP",)
STRUCTURE_ORDER = ("PORT", "AIRFIELD", "DATACENTER", "INDUSTRY", "POWER", "RADAR")
MIN_SPACING = 2          # preferred hex distance between structures
RADAR_SPACING = 3        # radars are spread further apart


class PlacementError(RuntimeError):
    """A start position cannot be built on this map (the attempt is retried)."""


def open_seas(w: WorldMap) -> Dict[Coord, int]:
    """Water hex -> sea id, for seas that are their own mirror image and big
    enough. Only these can hold a port pair that is connected by sea."""
    t = w.hexmap.terrain
    water = [c for c in t if t[c] is Terrain.WATER]
    comp_of: Dict[Coord, int] = {}
    comps = connected_components(water)
    for i, comp in enumerate(comps):
        for c in comp:
            comp_of[c] = i
    out: Dict[Coord, int] = {}
    for i, comp in enumerate(comps):
        if len(comp) >= w.params.world.min_open_sea and comp_of[w.mirror(comp[0])] == i:
            for c in comp:
                out[c] = i
    return out


def place_start(w: WorldMap) -> None:
    wp, st = w.params.world, w.params.start
    W, H = wp.width, wp.height
    t, elev = w.hexmap.terrain, w.elevation
    rivers = w.hexmap.rivers
    is_water = lambda c: t[c] is Terrain.WATER  # noqa: E731
    in_blue = lambda c: col_of(c) < W // 2  # noqa: E731
    coastal = lambda c: any(n in t and is_water(n) for n in neighbors(*c))  # noqa: E731

    # --- HQ: westmost inland, non-mountain land hex (centre-most row first).
    cands = [c for c in t if not is_water(c) and t[c] is not Terrain.MOUNTAIN
             and in_blue(c) and not coastal(c)]
    if not cands:
        raise PlacementError("no inland site for an HQ")
    hq = min(cands, key=lambda c: (col_of(c), abs(c[1] - H / 2), c[0]))

    # --- Home provinces: the HQ's, plus the nearest BLUE-seeded ones.
    n_half = wp.provinces_per_half
    hq_prov = w.province_of[hq]
    if hq_prov >= n_half:
        raise PlacementError("HQ lies in a province seeded in RED's half")
    cap = {p.id: p.capital for p in w.provinces}
    others = sorted((p for p in range(n_half) if p != hq_prov),
                    key=lambda p: (distance(cap[p], cap[hq_prov]), p))
    home_blue = (hq_prov,) + tuple(others[:wp.min_home_provinces - 1])
    if len(home_blue) < wp.min_home_provinces:
        raise PlacementError("not enough provinces for a home territory")
    home_red = tuple(w.mirror_province(p) for p in home_blue)
    w.home = {Side.BLUE: home_blue, Side.RED: home_red}
    w.hq = {Side.BLUE: hq, Side.RED: w.mirror(hq)}
    w.owner = {p.id: None for p in w.provinces}
    for p in home_blue:
        w.owner[p] = Side.BLUE
    for p in home_red:
        w.owner[p] = Side.RED

    home_hexes = sorted(c for p in home_blue for c in w.provinces[p].hexes if in_blue(c))
    d_hq = lambda c: distance(c, hq)  # noqa: E731
    sea = open_seas(w)
    urban = [c for c in t if t[c] is Terrain.URBAN and in_blue(c)]
    used: List[Coord] = [hq]

    def choose(pool: List[Coord], key, spacing: int = MIN_SPACING) -> Coord:
        pool = [c for c in pool if c not in used]
        spread = [c for c in pool if all(distance(c, u) >= spacing for u in used)]
        pool = spread or pool
        if not pool:
            raise PlacementError("no free site")
        return min(pool, key=key)

    flat_nb = lambda c: sum(1 for n in neighbors(*c)  # noqa: E731
                            if n in t and t[n] is Terrain.PLAIN and n not in rivers)
    chosen: List[Tuple[str, Coord]] = [("HQ", hq)]
    counts = dict(st.structures)
    for kind in STRUCTURE_ORDER:
        for _ in range(counts.get(kind, 0)):
            if kind == "PORT":
                pool = [c for c in home_hexes if not is_water(c)
                        and any(n in sea for n in neighbors(*c))]
                if not pool:
                    raise PlacementError("no port site on an open sea")
                c = choose(pool, lambda c: (d_hq(c), c))
            elif kind == "AIRFIELD":
                c = choose([c for c in home_hexes if t[c] is Terrain.PLAIN and c not in rivers],
                           lambda c: (-flat_nb(c), abs(d_hq(c) - 4), c))
            elif kind == "DATACENTER":
                c = choose([c for c in home_hexes if 2 <= d_hq(c) <= 4
                            and t[c] not in (Terrain.WATER, Terrain.MOUNTAIN)],
                           lambda c: (t[c] is not Terrain.PLAIN, abs(d_hq(c) - 3), c))
            elif kind == "INDUSTRY":
                c = choose([c for c in home_hexes
                            if t[c] not in (Terrain.WATER, Terrain.MOUNTAIN, Terrain.URBAN)],
                           lambda c: (min((distance(c, u) for u in urban), default=99),
                                      d_hq(c), c))
            elif kind == "POWER":
                c = choose([c for c in home_hexes if t[c] is Terrain.PLAIN and d_hq(c) >= 2],
                           lambda c: (abs(d_hq(c) - 5), c))
            else:  # RADAR: the highest hill or mountain, else the highest land
                high = [c for c in home_hexes if t[c] in (Terrain.HILL, Terrain.MOUNTAIN)]
                pool = high or [c for c in home_hexes if not is_water(c)]
                c = choose(pool, lambda c: (-elev[c], c), RADAR_SPACING)
            used.append(c)
            chosen.append((kind, c))

    structures: List[PlacedStructure] = []
    for side in (Side.BLUE, Side.RED):
        for kind, c in chosen:
            structures.append(PlacedStructure(kind, side, c if side is Side.BLUE else w.mirror(c)))
    w.structures = tuple(structures)

    # --- Forces: land near the HQ, air at the airfield, ships at the port.
    airfield = next((c for k, c in chosen if k == "AIRFIELD"), hq)
    port = next((c for k, c in chosen if k == "PORT"), None)
    blue_hexes = [c for c in t if in_blue(c)]
    land_ring = sorted((c for c in blue_hexes if not is_water(c)), key=lambda c: (d_hq(c), c))
    air_ring = sorted(blue_hexes, key=lambda c: (distance(c, airfield), c))
    sea_ring = (sorted((c for c in blue_hexes if c in sea),
                       key=lambda c: (distance(c, port), c)) if port else [])
    taken: Dict[str, set] = {"LAND": set(), "AIR": set(), "SEA": set()}
    units: List[Tuple[str, Coord]] = []
    for kind, n in st.forces:
        dom, ring = (("AIR", air_ring) if kind in AIR_UNITS else
                     ("SEA", sea_ring) if kind in SEA_UNITS else ("LAND", land_ring))
        if kind in SEA_UNITS and port is None:
            continue                           # no port, no ship
        for _ in range(n):
            spot = next((c for c in ring if c not in taken[dom]), None)
            if spot is None:
                raise PlacementError(f"no room for {kind}")
            taken[dom].add(spot)
            units.append((kind, spot))
    w.units = tuple(
        [PlacedUnit(k, Side.BLUE, c) for k, c in units]
        + [PlacedUnit(k, Side.RED, w.mirror(c)) for k, c in units])
