"""WorldMap: everything the generator produces, in one object.

Pure data plus a few lookup helpers. The generator (`generate.py`) fills it
in; the engine (from M3 on) and the viewer only read it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import params as P
from ..core.hexgrid import Coord, HexMap, Terrain, col_of, mirror_coord
from ..sim.units import Side


@dataclass(frozen=True)
class Province:
    id: int
    seed: Coord                    # the hex the province grew from
    hexes: Tuple[Coord, ...]       # sorted
    capital: Coord                 # city centre, else a cheap hex next to the seed
    has_city: bool
    value: int
    neighbours: Tuple[int, ...]    # provinces sharing an edge, sorted
    side: Side                     # which half the province was seeded in


@dataclass(frozen=True)
class Region:
    id: int
    provinces: Tuple[int, ...]
    bonus: int                     # per-turn bonus for holding all of them


@dataclass(frozen=True)
class PlacedStructure:
    kind: str                      # "HQ", "PORT", ... (names, as in params)
    side: Side
    coord: Coord


@dataclass(frozen=True)
class PlacedUnit:
    kind: str                      # UnitType name, ENGINEER included
    side: Side
    coord: Coord


@dataclass
class WorldMap:
    seed: int
    preset: str
    params: P.Params
    hexmap: HexMap
    elevation: Dict[Coord, float]
    cities: Tuple[Tuple[Coord, ...], ...]       # first hex of each is the centre
    river_paths: Tuple[Tuple[Coord, ...], ...]  # land hexes, then the mouth (water) hex
    road_paths: Tuple[Tuple[Coord, ...], ...]
    provinces: Tuple[Province, ...]
    province_of: Dict[Coord, int]               # land hexes only
    regions: Tuple[Region, ...]
    region_of: Dict[int, int]                   # province id -> region id
    # Filled in by placement:
    hq: Dict[Side, Coord] = field(default_factory=dict)
    home: Dict[Side, Tuple[int, ...]] = field(default_factory=dict)
    owner: Dict[int, Optional[Side]] = field(default_factory=dict)  # None = NEUTRAL
    structures: Tuple[PlacedStructure, ...] = ()
    units: Tuple[PlacedUnit, ...] = ()
    retries: int = 0                            # failed attempts before this one

    # -- geometry ---------------------------------------------------------

    @property
    def width(self) -> int:
        return self.params.world.width

    @property
    def height(self) -> int:
        return self.params.world.height

    def mirror(self, c: Coord) -> Coord:
        return mirror_coord(c, self.width, self.height)

    def half(self, c: Coord) -> Side:
        """BLUE holds the west half (col < W/2), RED the east half."""
        return Side.BLUE if col_of(c) < self.width // 2 else Side.RED

    # -- provinces and regions --------------------------------------------

    def mirror_province(self, pid: int) -> int:
        n = self.params.world.provinces_per_half
        return pid + n if pid < n else pid - n

    def mirror_region(self, rid: int) -> int:
        return rid ^ 1             # regions are created in (BLUE, RED) pairs

    # -- queries ----------------------------------------------------------

    def land_hexes(self) -> List[Coord]:
        t = self.hexmap.terrain
        return sorted(c for c, k in t.items() if k is not Terrain.WATER)

    def structure_at(self, c: Coord) -> Optional[PlacedStructure]:
        for s in self.structures:
            if s.coord == c:
                return s
        return None

    def summary(self) -> Dict[str, object]:
        t = self.hexmap.terrain
        mix: Dict[str, int] = {k.name: 0 for k in Terrain}
        for k in t.values():
            mix[k.name] += 1
        total = len(t)
        return {
            "seed": self.seed,
            "preset": self.preset,
            "size": f"{self.width}x{self.height}",
            "land_pct": 100.0 * (total - mix["WATER"]) / total,
            "terrain": mix,
            "provinces": len(self.provinces),
            "regions": len(self.regions),
            "cities": len(self.cities),
            "river_hexes": len(self.hexmap.rivers),
            "road_hexes": len(self.hexmap.roads),
            "retries": self.retries,
        }

    def fingerprint(self) -> str:
        """Hash of everything generated. Equal seeds must give equal hashes."""
        h = hashlib.sha256()
        t = self.hexmap.terrain
        h.update(repr(sorted((c, int(k)) for c, k in t.items())).encode())
        h.update(repr(sorted(self.hexmap.rivers)).encode())
        h.update(repr(sorted(self.hexmap.roads)).encode())
        h.update(repr(sorted(self.cities)).encode())
        h.update(repr([(p.id, p.hexes, p.capital, p.value, p.neighbours)
                       for p in self.provinces]).encode())
        h.update(repr([(r.id, r.provinces) for r in self.regions]).encode())
        h.update(repr(sorted((int(k), v) for k, v in self.hq.items())).encode())
        h.update(repr([(s.kind, int(s.side), s.coord) for s in self.structures]).encode())
        h.update(repr([(u.kind, int(u.side), u.coord) for u in self.units]).encode())
        h.update(repr(sorted((k, None if v is None else int(v))
                             for k, v in self.owner.items())).encode())
        return h.hexdigest()
