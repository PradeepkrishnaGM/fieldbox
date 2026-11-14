"""Hex grid math, terrain, and map.

Uses axial coordinates (q, r) with pointy-top hexes.
Pure stdlib -- no dependencies, so the engine stays trivially packageable.

`Domain` lives here rather than in `units` because it is fundamentally a
statement about terrain traversal: land cannot cross water, sea cannot leave
it, air ignores both. Putting it in `units` would make the import circular.

Performance note: terrain is immutable once `finalize()` runs, which lets us
precompute a flat adjacency list of (neighbour, entry_cost) pairs *per
domain*. A* then never touches a terrain dict, which is worth roughly an
order of magnitude because pathfinding dominates the cost of a turn.
"""

from __future__ import annotations

import heapq
from enum import IntEnum
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .. import params as P

Coord = Tuple[int, int]

# Axial neighbour offsets, clockwise from East.
DIRECTIONS: Tuple[Coord, ...] = (
    (1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1),
)


class Terrain(IntEnum):
    PLAIN = 0
    FOREST = 1
    HILL = 2
    URBAN = 3
    WATER = 4
    MOUNTAIN = 5          # added in M2; sorts after WATER to keep old values


class Domain(IntEnum):
    """Where a unit lives. Decides movement, stacking and vulnerability."""
    LAND = 0
    AIR = 1
    SEA = 2


# Tunables live in fieldbox.params (keyed by name); converted to enums here.
IMPASSABLE = P.IMPASSABLE
ROAD_COST = P.ROAD_COST
RIVER_COST = P.RIVER_COST  # extra land cost to enter a river hex without a bridge
AIR_COST = P.AIR_COST
SEA_COST = P.SEA_COST

# Movement point cost to ENTER a hex, per domain.
MOVE_COST: Dict[Domain, Dict[Terrain, float]] = {
    Domain[d]: {Terrain[t]: c for t, c in costs.items()}
    for d, costs in P.MOVE_COST.items()
}

# Multiplier applied to a defender's combat power in this terrain. Only land
# units benefit: there is nothing to hide behind at altitude or at sea.
DEF_MULT: Dict[Terrain, float] = {Terrain[t]: m for t, m in P.DEF_MULT.items()}

TERRAIN_CHAR: Dict[Terrain, str] = {
    Terrain.PLAIN: ".",
    Terrain.FOREST: "f",
    Terrain.HILL: "^",
    Terrain.URBAN: "#",
    Terrain.WATER: "~",
    Terrain.MOUNTAIN: "%",
}

_MISS = object()
_DISC_CACHE: Dict[int, Tuple[Coord, ...]] = {}


def disc_offsets(radius: int) -> Tuple[Coord, ...]:
    """All axial offsets within `radius` hexes, cached.

    Scanning the whole map to find hexes near a unit is O(map); enumerating
    the disc directly is O(radius^2) -- 37 hexes instead of 252 at radius 3.
    """
    cached = _DISC_CACHE.get(radius)
    if cached is not None:
        return cached
    out = []
    for dq in range(-radius, radius + 1):
        lo = max(-radius, -dq - radius)
        hi = min(radius, -dq + radius)
        for dr in range(lo, hi + 1):
            out.append((dq, dr))
    result = tuple(out)
    _DISC_CACHE[radius] = result
    return result


def col_of(c: Coord) -> int:
    """Offset column of an axial coordinate (the map is a W-column rectangle)."""
    return c[0] + c[1] // 2


def mirror_coord(c: Coord, width: int, height: int) -> Coord:
    """180-degree rotation of the map onto itself (even width and height).
    Maps neighbours to neighbours and sends column col -> width-1-col."""
    return (width - height // 2 - c[0], height - 1 - c[1])


def neighbors(q: int, r: int) -> Iterator[Coord]:
    for dq, dr in DIRECTIONS:
        yield q + dq, r + dr


def distance(a: Coord, b: Coord) -> int:
    """Hex distance in axial coordinates."""
    dq = a[0] - b[0]
    dr = a[1] - b[1]
    return (abs(dq) + abs(dq + dr) + abs(dr)) // 2


class HexMap:
    """Rectangular hex map addressed by axial coords.

    Row r spans q in [-(r // 2), width - (r // 2)), so it renders as a tidy
    rectangle rather than a sheared parallelogram.
    """

    __slots__ = (
        "width", "height", "terrain", "roads", "rivers",
        "_adj", "_cost", "_def", "_path_cache", "_final", "symmetric",
    )

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.terrain: Dict[Coord, Terrain] = {}
        self.roads: set = set()
        self.rivers: set = set()   # overlay flag on land hexes (not a terrain)
        self._adj: Dict[Domain, Dict[Coord, Tuple[Tuple[Coord, float], ...]]] = {}
        self._cost: Dict[Domain, Dict[Coord, float]] = {}
        self._def: Dict[Coord, float] = {}
        self._path_cache: Dict[tuple, Optional[List[Coord]]] = {}
        self._final = False
        # True once the map is known to be point-symmetric (set by the world
        # generator). Paths are then computed in a canonical orientation, so a
        # route and its mirror image are always mirror images: equal ties are
        # not broken in favour of one side.
        self.symmetric = False
        for r in range(height):
            offset = r // 2
            for col in range(width):
                self.terrain[(col - offset, r)] = Terrain.PLAIN
        self.finalize()

    # -- build ------------------------------------------------------------

    def finalize(self) -> "HexMap":
        """Freeze terrain and precompute lookup tables, one set per domain.
        Must be called after any direct mutation of `terrain` or `roads`."""
        roads = self.roads
        rivers = self.rivers
        self._cost = {}
        self._adj = {}
        for domain in Domain:
            table = MOVE_COST[domain]
            cost: Dict[Coord, float] = {}
            for coord, terr in self.terrain.items():
                base = table[terr]
                # Roads only help ground movement.
                # A road on a river hex is a bridge: no river penalty.
                if domain is Domain.LAND and base < IMPASSABLE:
                    if coord in roads:
                        base = ROAD_COST
                    elif coord in rivers:
                        base += RIVER_COST
                cost[coord] = base
            adj: Dict[Coord, Tuple[Tuple[Coord, float], ...]] = {}
            for coord in self.terrain:
                if cost[coord] >= IMPASSABLE:
                    continue
                entries = []
                for n in neighbors(*coord):
                    c = cost.get(n)
                    if c is not None and c < IMPASSABLE:
                        entries.append((n, c))
                adj[coord] = tuple(entries)
            self._cost[domain] = cost
            self._adj[domain] = adj
        self._def = {c: DEF_MULT[t] for c, t in self.terrain.items()}
        self._path_cache = {}
        self._final = True
        return self

    def set_terrain(self, coord: Coord, terrain: Terrain) -> None:
        """Mutate terrain safely. Direct assignment to `.terrain` also works
        but then you must call `finalize()` yourself."""
        self.terrain[coord] = terrain
        self._final = False

    def add_road(self, coord: Coord) -> None:
        self.roads.add(coord)
        self._final = False

    def add_river(self, coord: Coord) -> None:
        self.rivers.add(coord)
        self._final = False

    def _ensure(self) -> None:
        if not self._final:
            self.finalize()

    # -- queries ----------------------------------------------------------

    def in_bounds(self, coord: Coord) -> bool:
        return coord in self.terrain

    def get(self, coord: Coord) -> Terrain:
        return self.terrain.get(coord, Terrain.WATER)

    def passable(self, coord: Coord, domain: Domain = Domain.LAND) -> bool:
        self._ensure()
        return self._cost[domain].get(coord, IMPASSABLE) < IMPASSABLE

    def move_cost(self, coord: Coord, domain: Domain = Domain.LAND) -> float:
        self._ensure()
        return self._cost[domain].get(coord, IMPASSABLE)

    def def_mult(self, coord: Coord, domain: Domain = Domain.LAND) -> float:
        """Terrain protects ground troops only."""
        if domain is not Domain.LAND:
            return 1.0
        self._ensure()
        return self._def.get(coord, 1.0)

    def all_coords(self) -> List[Coord]:
        return list(self.terrain.keys())

    def adjacency(self, domain: Domain = Domain.LAND):
        """Raw (neighbour, entry_cost) table. Hot loops should use this
        rather than passable_neighbors + move_cost, which repeat the same
        dict lookups and the finalize check on every hex."""
        self._ensure()
        return self._adj[domain]

    def passable_neighbors(
        self, coord: Coord, domain: Domain = Domain.LAND
    ) -> Iterator[Coord]:
        self._ensure()
        for n, _ in self._adj[domain].get(coord, ()):
            yield n

    # -- pathfinding ------------------------------------------------------

    def find_path(
        self,
        start: Coord,
        goal: Coord,
        blocked: Optional[frozenset] = None,
        domain: Domain = Domain.LAND,
    ) -> Optional[List[Coord]]:
        """A* over movement cost. Returns the hexes AFTER `start`, or None.
        On a symmetric map the query and its 180-degree mirror are answered
        from one canonical search, so the two sides get mirrored routes."""
        if self.symmetric:
            w, h = self.width, self.height
            ms, mg = mirror_coord(start, w, h), mirror_coord(goal, w, h)
            if (ms, mg) < (start, goal):
                mb = frozenset(mirror_coord(c, w, h) for c in blocked) if blocked else blocked
                path = self._find_path(ms, mg, mb, domain)
                return None if path is None else [mirror_coord(c, w, h) for c in path]
        return self._find_path(start, goal, blocked, domain)

    def _find_path(
        self,
        start: Coord,
        goal: Coord,
        blocked: Optional[frozenset] = None,
        domain: Domain = Domain.LAND,
    ) -> Optional[List[Coord]]:
        """A* over movement cost. Returns the hexes AFTER `start`, or None.

        `goal` is always permitted as a destination even if it appears in
        `blocked`, so a caller can path *to* an enemy-held hex and drop the
        final step to close to contact -- one search instead of six.
        """
        if start == goal:
            return []
        self._ensure()
        adj = self._adj[domain]
        if goal not in adj:
            return None
        blocked = blocked or frozenset()
        cacheable = not blocked
        key = (start, goal, domain)
        cached = self._path_cache.get(key, _MISS)
        if cached is not _MISS:
            if cacheable:
                return cached
            # An unblocked-optimal route that happens to be clear of units is
            # still optimal for the blocked problem: blocking only removes
            # options, so nothing cheaper can exist.
            if cached is not None and not any(h in blocked for h in cached[:-1]):
                return cached

        gq, gr = goal
        came_from: Dict[Coord, Coord] = {}
        best: Dict[Coord, float] = {start: 0.0}
        frontier: List[tuple] = [(0.0, 0.0, start)]
        pop = heapq.heappop
        push = heapq.heappush
        found = False

        while frontier:
            _, cost_here, current = pop(frontier)
            if current == goal:
                found = True
                break
            if cost_here > best.get(current, 1e18):
                continue
            for nxt, step_cost in adj.get(current, ()):
                if nxt in blocked and nxt != goal:
                    continue
                new_cost = cost_here + step_cost
                if new_cost < best.get(nxt, 1e18):
                    best[nxt] = new_cost
                    came_from[nxt] = current
                    nq, nr = nxt
                    dq = nq - gq
                    dr = nr - gr
                    # Admissible: no step can cost less than a road hex.
                    h = ((abs(dq) + abs(dq + dr) + abs(dr)) // 2) * ROAD_COST
                    push(frontier, (new_cost + h, new_cost, nxt))

        if not found:
            if cacheable:
                self._path_cache[key] = None
            return None

        path: List[Coord] = []
        node = goal
        while node != start:
            path.append(node)
            node = came_from[node]
        path.reverse()
        if cacheable:
            self._path_cache[key] = path
        return path

    def clear_cache(self) -> None:
        self._path_cache.clear()


def connected_components(coords: Iterable[Coord]) -> List[List[Coord]]:
    """6-neighbour connected components. Deterministic: components are found
    in sorted order, so equal inputs always give equal outputs."""
    remaining = set(coords)
    out: List[List[Coord]] = []
    for start in sorted(remaining):
        if start not in remaining:
            continue
        comp = [start]
        remaining.discard(start)
        stack = [start]
        while stack:
            cur = stack.pop()
            for n in neighbors(*cur):
                if n in remaining:
                    remaining.discard(n)
                    comp.append(n)
                    stack.append(n)
        out.append(comp)
    return out
