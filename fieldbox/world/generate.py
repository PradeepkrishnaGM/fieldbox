"""World generation: noise, terrain, rivers, cities, roads.

Every map is exactly point-symmetric (180-degree rotation), so both sides
get the same ground. The recipe: build the BLUE half's features, then mirror.
All randomness comes from `derive_seed` / `make_rng` with named parts.
"""

from __future__ import annotations

import heapq
import math
from typing import Dict, List, Optional, Set, Tuple

from .. import params as P
from ..core.hexgrid import (Coord, HexMap, Terrain, col_of, connected_components,
                            distance, mirror_coord, neighbors, MOVE_COST,
                            Domain, RIVER_COST)
from ..core.rng import derive_seed, make_rng
from .worldmap import WorldMap

MAX_TRIES = 50                 # attempts before giving up on a seed
ROAD_REUSE_COST = 0.3          # routing cost of a hex that already has a road
SOURCE_POOL_FRACTION = 0.25    # river sources come from the top quarter by elevation
RIVER_MAX_STEPS = 40           # a river that has not reached water by now is dropped
RIVER_SOURCE_SPACING = 3       # minimum hex distance between river sources


class WorldGenError(RuntimeError):
    """No valid world found for this seed within MAX_TRIES."""


# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------


class FBm:
    """Value noise summed over octaves, scaled to [0, 1].

    Lattice values are hashed from the seed, so there is no RNG state and the
    field can be sampled in any order.
    """

    def __init__(self, seed: int, name: str, scale: float, octaves: int) -> None:
        self.seed, self.name, self.scale, self.octaves = seed, name, scale, octaves
        self._cache: Dict[tuple, float] = {}

    def _node(self, o: int, ix: int, iy: int) -> float:
        key = (o, ix, iy)
        v = self._cache.get(key)
        if v is None:
            v = derive_seed(self.seed, self.name, o, ix, iy) / 2.0 ** 64
            self._cache[key] = v
        return v

    def __call__(self, x: float, y: float) -> float:
        total = norm = 0.0
        for o in range(self.octaves):
            cell = self.scale / (2 ** o)
            gx, gy = x / cell, y / cell
            ix, iy = math.floor(gx), math.floor(gy)
            fx, fy = gx - ix, gy - iy
            fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
            top = self._node(o, ix, iy) * (1 - fx) + self._node(o, ix + 1, iy) * fx
            bot = self._node(o, ix, iy + 1) * (1 - fx) + self._node(o, ix + 1, iy + 1) * fx
            amp = 0.5 ** o
            total += amp * (top * (1 - fy) + bot * fy)
            norm += amp
        return total / norm


def _xy(c: Coord) -> Tuple[float, float]:
    """Pointy-top hex centre in hex-width units (isotropic sampling)."""
    return c[0] + c[1] / 2.0, c[1] * 0.8660254037844386


def _symmetric_field(coords, mirror, fbm: FBm) -> Dict[Coord, float]:
    raw = {c: fbm(*_xy(c)) for c in coords}
    return {c: (raw[c] + raw[mirror(c)]) / 2.0 for c in coords}


# ---------------------------------------------------------------------------
# Terrain
# ---------------------------------------------------------------------------


def _land_cost(terrain: Dict[Coord, Terrain], rivers: Set[Coord], c: Coord) -> float:
    return MOVE_COST[Domain.LAND][terrain[c]] + (RIVER_COST if c in rivers else 0.0)


def make_terrain(seed: int, wp: P.WorldParams):
    """Steps 1-4: elevation, sea level, relief, forests.
    Returns (terrain, elevation)."""
    W, H = wp.width, wp.height
    mirror = lambda c: mirror_coord(c, W, H)  # noqa: E731
    coords = [(col - r // 2, r) for r in range(H) for col in range(W)]

    # 1. Elevation: symmetric fBm, lowered towards the north and south edges.
    elev = _symmetric_field(coords, mirror,
                            FBm(derive_seed(seed, "elev"), "e", wp.noise_scale, 4))
    mid = (H - 1) / 2.0
    for c in coords:
        elev[c] -= wp.edge_falloff * (abs(c[1] - mid) / mid) ** 2

    # 1b. Central sea basin: an east-west ellipse at the map centre, so a sea
    # that is its own mirror image always exists for the ports.
    xy = {c: _xy(c) for c in coords}
    cx = sum(p[0] for p in xy.values()) / len(xy)
    cy = sum(p[1] for p in xy.values()) / len(xy)
    a = wp.central_sea_length * W / 2.0
    b = wp.central_sea_width * (H * 0.8660254037844386) / 2.0
    ell = {c: ((xy[c][0] - cx) / a) ** 2 + ((xy[c][1] - cy) / b) ** 2 for c in coords}
    raw_bias = {c: wp.central_sea_depth * (1 - ell[c]) ** 2 if ell[c] < 1 else 0.0
                for c in coords}
    for c in coords:       # symmetrise like the noise, so ties can never break
        elev[c] -= (raw_bias[c] + raw_bias[mirror(c)]) / 2.0

    # 2. Sea level: the water_fraction quantile. Mirror pairs have identical
    # elevation, so strict "<" classifies both hexes of a pair the same way.
    ordered = sorted(elev.values())
    level = ordered[int(wp.water_fraction * len(ordered))]
    terrain: Dict[Coord, Terrain] = {
        c: (Terrain.WATER if elev[c] < level else Terrain.PLAIN) for c in coords}
    core = wp.central_sea_core ** 2
    for c in coords:       # force the ellipse core to water (symmetric by construction)
        if (ell[c] + ell[mirror(c)]) / 2.0 < core:
            terrain[c] = Terrain.WATER
    for comp in connected_components(c for c in coords if terrain[c] is not Terrain.WATER):
        if len(comp) < wp.min_island:
            for c in comp:
                terrain[c] = Terrain.WATER
    for comp in connected_components(c for c in coords if terrain[c] is Terrain.WATER):
        if len(comp) < wp.min_lake:
            for c in comp:
                terrain[c] = Terrain.PLAIN

    # 3. Relief: highest land is MOUNTAIN, the next band HILL.
    land = sorted((c for c in coords if terrain[c] is not Terrain.WATER),
                  key=lambda c: -elev[c])
    n_m = int(wp.mountain_fraction * len(land))
    n_h = int(wp.hill_fraction * len(land))
    thr_m = elev[land[n_m - 1]] if n_m else math.inf
    thr_h = elev[land[n_m + n_h - 1]] if n_m + n_h else math.inf
    for c in land:
        if elev[c] >= thr_m and elev[c] >= level:
            terrain[c] = Terrain.MOUNTAIN
        elif elev[c] >= thr_h and elev[c] >= level:
            terrain[c] = Terrain.HILL

    # 4. Moisture: wet plains become forest.
    moist = _symmetric_field(coords, mirror,
                             FBm(derive_seed(seed, "moist"), "m", wp.noise_scale * 1.3, 3))
    for c in coords:
        if terrain[c] is Terrain.PLAIN and moist[c] > wp.forest_threshold:
            terrain[c] = Terrain.FOREST
    return terrain, elev


# ---------------------------------------------------------------------------
# Rivers
# ---------------------------------------------------------------------------


def make_rivers(seed: int, wp: P.WorldParams, terrain, elev):
    """Step 5. Returns (paths, river_hexes). Each path is the land hexes from
    the source down, then the water hex it empties into."""
    W, H = wp.width, wp.height
    rng = make_rng(seed, "rivers")
    t = terrain
    blue_land = sorted((c for c in t if t[c] is not Terrain.WATER and col_of(c) < W // 2),
                       key=lambda c: (-elev[c], c))
    pool = blue_land[:max(1, int(len(blue_land) * SOURCE_POOL_FRACTION))]
    sources: List[Coord] = []
    while pool and len(sources) < wp.rivers_per_half:
        s = pool.pop(rng.randrange(len(pool)))
        if all(distance(s, o) >= RIVER_SOURCE_SPACING for o in sources):
            sources.append(s)

    paths: List[Tuple[Coord, ...]] = []
    for src in sources:
        path, visited, cur = [src], {src}, src
        mouth: Optional[Coord] = None
        for _ in range(RIVER_MAX_STEPS):
            nbrs = [n for n in neighbors(*cur) if n in t]
            water = sorted(n for n in nbrs if t[n] is Terrain.WATER)
            if water:
                mouth = water[0]
                break
            options = sorted((n for n in nbrs if n not in visited), key=lambda n: (elev[n], n))
            if not options:
                break
            cur = options[0]       # steepest descent; at a minimum, lowest unvisited
            visited.add(cur)
            path.append(cur)
        if mouth is not None and len(path) >= wp.min_river_len:
            paths.append(tuple(path) + (mouth,))
    mirrored = [tuple(mirror_coord(c, W, H) for c in p) for p in paths]
    all_paths = paths + mirrored
    hexes = {c for p in all_paths for c in p[:-1]}
    return all_paths, hexes


# ---------------------------------------------------------------------------
# Cities and roads
# ---------------------------------------------------------------------------


def make_cities(seed: int, wp: P.WorldParams, terrain, rivers):
    """Step 6. Marks URBAN hexes in `terrain`. Returns cities: BLUE's first,
    then their mirrors (city i and city i+n are a pair)."""
    W, H = wp.width, wp.height
    rng = make_rng(seed, "cities")
    t = terrain
    blue = [c for c in sorted(t) if col_of(c) < W // 2]

    def score(c: Coord) -> float:
        nb = [n for n in neighbors(*c) if n in t]
        flat = sum(1 for n in nb if t[n] is Terrain.PLAIN) / 6.0
        coast = 0.6 if any(t[n] is Terrain.WATER for n in nb) else 0.0
        river = 0.6 if c in rivers or any(n in rivers for n in nb) else 0.0
        return flat + coast + river + rng.random() * 0.5

    cands = sorted(((score(c), c) for c in blue if t[c] in (Terrain.PLAIN, Terrain.HILL)),
                   key=lambda x: (-x[0], x[1]))
    centres: List[Coord] = []
    taken: List[Coord] = []        # centres plus their mirrors, for spacing
    for _, c in cands:
        if len(centres) >= wp.cities_per_half:
            break
        if all(distance(c, o) >= wp.city_spacing for o in taken):
            centres.append(c)
            taken += [c, mirror_coord(c, W, H)]

    cities: List[Tuple[Coord, ...]] = []
    for centre in centres:
        hexes = [centre]
        for _ in range(rng.randint(0, 2)):
            grow = sorted({n for h in hexes for n in neighbors(*h)
                           if n in t and col_of(n) < W // 2 and n not in hexes
                           and t[n] in (Terrain.PLAIN, Terrain.FOREST, Terrain.HILL)})
            if not grow:
                break
            hexes.append(rng.choice(grow))
        cities.append(tuple(hexes))
    cities += [tuple(mirror_coord(c, W, H) for c in city) for city in cities]
    for city in cities:
        for c in city:
            t[c] = Terrain.URBAN
    return tuple(cities)


def _route(terrain, rivers, roads, a: Coord, b: Coord) -> Optional[List[Coord]]:
    """A* between two land hexes; existing road hexes are cheap."""
    def cost(c: Coord) -> float:
        return ROAD_REUSE_COST if c in roads else _land_cost(terrain, rivers, c)
    best = {a: 0.0}
    came: Dict[Coord, Coord] = {}
    frontier = [(ROAD_REUSE_COST * distance(a, b), 0.0, a)]
    while frontier:
        _, g, cur = heapq.heappop(frontier)
        if cur == b:
            path = [b]
            while path[-1] != a:
                path.append(came[path[-1]])
            return path[::-1]
        if g > best.get(cur, 1e18):
            continue
        for n in neighbors(*cur):
            if n not in terrain or terrain[n] is Terrain.WATER:
                continue
            ng = g + cost(n)
            if ng < best.get(n, 1e18):
                best[n], came[n] = ng, cur
                heapq.heappush(frontier, (ng + ROAD_REUSE_COST * distance(n, b), ng, n))
    return None


def make_roads(wp: P.WorldParams, terrain, rivers, cities):
    """Step 7. Each BLUE city is joined to its two nearest cities (either
    half); every path is mirrored so the network stays symmetric."""
    W, H = wp.width, wp.height
    centres = [c[0] for c in cities]
    n_blue = len(cities) // 2
    pairs = set()
    for i in range(n_blue):
        near = sorted((j for j in range(len(centres)) if j != i),
                      key=lambda j: (distance(centres[i], centres[j]), j))[:2]
        pairs.update((min(i, j), max(i, j)) for j in near)
    roads: Set[Coord] = set()
    paths: List[Tuple[Coord, ...]] = []
    for i, j in sorted(pairs):
        path = _route(terrain, rivers, roads, centres[i], centres[j])
        if path is None:
            continue
        mirrored = [mirror_coord(c, W, H) for c in path]
        for p in (path, mirrored):
            paths.append(tuple(p))
            roads.update(p)
    return paths, roads


# ---------------------------------------------------------------------------
# Orchestration and validation
# ---------------------------------------------------------------------------


def _attempt(seed: int, preset: str, params: P.Params) -> WorldMap:
    from .provinces import build_provinces
    from .placement import place_start
    wp = params.world
    terrain, elev = make_terrain(seed, wp)
    river_paths, rivers = make_rivers(seed, wp, terrain, elev)
    cities = make_cities(seed, wp, terrain, rivers)
    road_paths, roads = make_roads(wp, terrain, rivers, cities)

    hexmap = HexMap(wp.width, wp.height)
    hexmap.terrain.update(terrain)
    hexmap.rivers.update(rivers)
    hexmap.roads.update(roads)
    hexmap.finalize()
    hexmap.symmetric = True        # generated maps are point-symmetric (validated below)

    provinces, province_of, regions, region_of = build_provinces(
        wp, terrain, rivers, cities)
    w = WorldMap(seed=seed, preset=preset, params=params, hexmap=hexmap,
                 elevation=elev, cities=cities, river_paths=tuple(river_paths),
                 road_paths=tuple(road_paths), provinces=provinces,
                 province_of=province_of, regions=regions, region_of=region_of)
    place_start(w)
    return w


def validate(w: WorldMap) -> Optional[str]:
    """Returns the reason a world is unfit, or None when it is fine (step 8)."""
    from ..sim.units import Side
    wp = w.params.world
    t = w.hexmap.terrain
    land = sum(1 for k in t.values() if k is not Terrain.WATER) / len(t)
    if abs(land - (1.0 - wp.water_fraction)) > 0.05:
        return f"land fraction {land:.2f} off target"
    if len(w.home[Side.BLUE]) < wp.min_home_provinces:
        return "too few home provinces"

    def reach(start, passable):
        seen, stack = {start}, [start]
        while stack:
            for n in neighbors(*stack.pop()):
                if n in t and n not in seen and passable(n):
                    seen.add(n)
                    stack.append(n)
        return seen

    is_land = lambda n: t[n] is not Terrain.WATER  # noqa: E731
    if w.hq[Side.RED] not in reach(w.hq[Side.BLUE], is_land):
        return "HQs not connected by land"
    ports = {s.side: s.coord for s in w.structures if s.kind == "PORT"}
    if Side.BLUE in ports and Side.RED in ports:
        seas = [n for n in neighbors(*ports[Side.BLUE]) if n in t and t[n] is Terrain.WATER]
        far = {n for n in neighbors(*ports[Side.RED]) if n in t and t[n] is Terrain.WATER}
        if not seas or not far & reach(seas[0], lambda n: t[n] is Terrain.WATER):
            return "ports not connected by sea"
    return None


def generate_world(seed: int, preset: str = "standard",
                   params: Optional[P.Params] = None) -> WorldMap:
    """Generate a valid, symmetric world for `seed`. A failed attempt is
    retried with derive_seed(seed, "retry", k); `retries` records how many."""
    from .placement import PlacementError
    params = params or P.PRESETS[preset]
    reasons: List[str] = []
    for k in range(MAX_TRIES):
        attempt_seed = seed if k == 0 else derive_seed(seed, "retry", k)
        try:
            w = _attempt(attempt_seed, preset, params)
            bad = validate(w)
        except PlacementError as e:
            bad = f"placement: {e}"
        if bad is None:
            w.seed, w.retries = seed, k
            return w
        reasons.append(bad)
    raise WorldGenError(f"seed {seed} ({preset}): no valid world in {MAX_TRIES} tries; "
                        f"last reasons: {reasons[-3:]}")
