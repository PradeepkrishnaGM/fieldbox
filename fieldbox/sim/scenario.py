"""Scenario generation.

Procedural but seeded, so a scenario seed reproduces exactly. Varying the
scenario seed while holding the match seed fixed is how you test agent
generalisation rather than map memorisation.

Two map templates exist because naval units need somewhere to float. The
default generator scatters small ponds, which would strand a ship on turn
one -- `coastal` carves a connected sea along one edge instead.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..core.hexgrid import Coord, Domain, HexMap, Terrain, neighbors
from .units import STATS, Side, Unit, UnitType

OrderOfBattle = Tuple[Tuple[UnitType, int], ...]

# Force presets. Keeping these named makes it obvious in an experiment log
# which force mix produced a result.
OOB_COMBINED_ARMS: OrderOfBattle = (
    (UnitType.INFANTRY, 4),
    (UnitType.TANK, 2),
    (UnitType.IFV, 2),
    (UnitType.RECON, 1),
    (UnitType.ARTILLERY, 1),
    (UnitType.SAM, 1),
    (UnitType.HELICOPTER, 1),
)

OOB_ARMOURED: OrderOfBattle = (
    (UnitType.TANK, 4),
    (UnitType.IFV, 3),
    (UnitType.INFANTRY, 2),
    (UnitType.RECON, 1),
    (UnitType.ARTILLERY, 1),
)

OOB_AIR_LAND: OrderOfBattle = (
    (UnitType.INFANTRY, 3),
    (UnitType.TANK, 2),
    (UnitType.IFV, 1),
    (UnitType.ARTILLERY, 1),
    (UnitType.SAM, 2),
    (UnitType.HELICOPTER, 1),
    (UnitType.JET, 1),
)

OOB_LITTORAL: OrderOfBattle = (
    (UnitType.INFANTRY, 3),
    (UnitType.TANK, 1),
    (UnitType.IFV, 1),
    (UnitType.ARTILLERY, 1),
    (UnitType.SAM, 1),
    (UnitType.SHIP, 2),
    (UnitType.JET, 1),
)

# The v0.1 roster, kept so older results remain reproducible.
OOB_LEGACY: OrderOfBattle = (
    (UnitType.INFANTRY, 4),
    (UnitType.TANK, 2),
    (UnitType.RECON, 1),
    (UnitType.ARTILLERY, 1),
)

PRESETS: Dict[str, OrderOfBattle] = {
    "combined_arms": OOB_COMBINED_ARMS,
    "armoured": OOB_ARMOURED,
    "air_land": OOB_AIR_LAND,
    "littoral": OOB_LITTORAL,
    "legacy": OOB_LEGACY,
}

TEMPLATES = ("inland", "coastal")


@dataclass
class Scenario:
    # Width must be ODD for the map to be fair. Deployment zones are two
    # columns deep at each edge, so the objective column is equidistant only
    # when width - 1 is even. At width 18 the centre column sat one hex
    # closer to RED, which is worth roughly a 90/10 win rate in a mirror
    # match because whoever reaches an objective first gets to entrench.
    width: int = 19
    height: int = 14
    max_turns: int = 30
    map_seed: int = 1
    template: str = "inland"
    oob: OrderOfBattle = OOB_COMBINED_ARMS
    objectives: Dict[Coord, int] = field(default_factory=dict)
    _terrain: Dict[Coord, Terrain] = field(default_factory=dict, repr=False)
    _roads: set = field(default_factory=set, repr=False)
    _units: List[Unit] = field(default_factory=list, repr=False)
    _map_cache: object = field(default=None, repr=False)

    @classmethod
    def preset(cls, name: str, **kwargs) -> "Scenario":
        """Build a scenario from a named force mix.

        Naval forces imply a coastal map -- a ship on the inland template has
        nowhere to go.
        """
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; have {sorted(PRESETS)}")
        oob = PRESETS[name]
        needs_sea = any(
            STATS[t].domain is Domain.SEA for t, _ in oob
        )
        kwargs.setdefault("template", "coastal" if needs_sea else "inland")
        return cls(oob=oob, **kwargs)

    def __post_init__(self) -> None:
        if self.template not in TEMPLATES:
            raise ValueError(f"unknown template {self.template!r}")
        self._generate()

    # -- generation -------------------------------------------------------

    def _generate(self) -> None:
        rng = random.Random(self.map_seed)
        base = HexMap(self.width, self.height)
        coords = base.all_coords()

        for terrain, blobs, size in (
            (Terrain.FOREST, 5, 7),
            (Terrain.HILL, 4, 5),
            (Terrain.URBAN, 3, 3),
            (Terrain.WATER, 2, 4),
        ):
            for _ in range(blobs):
                origin = rng.choice(coords)
                frontier = [origin]
                painted = 0
                while frontier and painted < size:
                    cur = frontier.pop(rng.randrange(len(frontier)))
                    if not base.in_bounds(cur):
                        continue
                    base.terrain[cur] = terrain
                    painted += 1
                    for n in neighbors(*cur):
                        if base.in_bounds(n) and rng.random() < 0.6:
                            frontier.append(n)

        if self.template == "coastal":
            self._carve_sea(base)

        # Keep the deployment strips clear so nobody starts in a lake.
        for coord in coords:
            col = coord[0] + coord[1] // 2
            row = coord[1]
            if (col <= 1 or col >= self.width - 2) and not self._is_sea_row(row):
                base.terrain[coord] = Terrain.PLAIN

        # A lateral road across the middle: cheap mobility corridor.
        mid_row = self.height // 2
        for col in range(self.width):
            coord = (col - mid_row // 2, mid_row)
            if base.in_bounds(coord) and base.get(coord) is not Terrain.WATER:
                base.roads.add(coord)

        base.finalize()
        self._terrain = dict(base.terrain)
        self._roads = set(base.roads)

        if not self.objectives:
            self.objectives = self._place_objectives(base, rng)
        self._units = self._place_forces(base, rng)

    def _sea_rows(self) -> range:
        """Rows given over to open water on the coastal template."""
        return range(self.height - 3, self.height)

    def _is_sea_row(self, row: int) -> bool:
        return self.template == "coastal" and row in self._sea_rows()

    def _carve_sea(self, base: HexMap) -> None:
        """A connected sea along the southern edge, so ships can manoeuvre."""
        for row in self._sea_rows():
            offset = row // 2
            for col in range(self.width):
                coord = (col - offset, row)
                if base.in_bounds(coord):
                    base.terrain[coord] = Terrain.WATER

    def _place_objectives(self, base: HexMap, rng: random.Random) -> Dict[Coord, int]:
        """Objectives sit on ground: only land units can capture them.

        The centre column is equidistant from both deployment zones only for
        odd widths -- see the note on `width`.
        """
        mid_row = self.height // 2
        centre_col = self.width // 2
        land_rows = [r for r in range(1, self.height - 1) if not self._is_sea_row(r)]
        if not land_rows:
            land_rows = [mid_row]
        picks: Dict[Coord, int] = {}
        wanted = [
            (centre_col, mid_row, 3),
            (centre_col, max(land_rows[0], mid_row - 4), 2),
            (centre_col, min(land_rows[-1], mid_row + 4), 2),
        ]
        for col, row, value in wanted:
            if row not in land_rows:
                row = min(land_rows, key=lambda r: abs(r - row))
            coord = (col - row // 2, row)
            if not base.in_bounds(coord):
                continue
            if base.get(coord) is Terrain.WATER:
                base.terrain[coord] = Terrain.URBAN
            picks[coord] = value
        base.finalize()
        return picks

    def _deployment_slots(
        self, base: HexMap, side: Side, domain: Domain
    ) -> List[Coord]:
        """Hexes a unit of this domain may start on, near its own edge."""
        depth = 2 if domain is not Domain.SEA else 4
        if side is Side.BLUE:
            cols = range(0, depth)
        else:
            cols = range(self.width - depth, self.width)
        slots = []
        for col in cols:
            for row in range(self.height):
                coord = (col - row // 2, row)
                if not base.in_bounds(coord):
                    continue
                if base.passable(coord, domain):
                    slots.append(coord)
        return slots

    def _place_forces(self, base: HexMap, rng: random.Random) -> List[Unit]:
        units: List[Unit] = []
        uid = 0
        for side in (Side.BLUE, Side.RED):
            taken: Dict[Domain, set] = {d: set() for d in Domain}
            pools: Dict[Domain, List[Coord]] = {}
            for domain in Domain:
                pool = self._deployment_slots(base, side, domain)
                rng.shuffle(pool)
                pools[domain] = pool
            for utype, count in self.oob:
                domain = STATS[utype].domain
                for _ in range(count):
                    pool = pools[domain]
                    coord = None
                    while pool:
                        candidate = pool.pop()
                        # Stacking is by domain, so two land units may not
                        # share a hex but a helicopter may fly over one.
                        if candidate in taken[domain]:
                            continue
                        coord = candidate
                        break
                    if coord is None:
                        # No legal deployment hex (e.g. a ship on an inland
                        # map). Skip rather than place it somewhere illegal.
                        continue
                    taken[domain].add(coord)
                    units.append(Unit(uid, side, utype, coord[0], coord[1]))
                    uid += 1
        return units

    # -- accessors used by Game -------------------------------------------

    def build_map(self, copy: bool = False) -> HexMap:
        """Shared by default.

        Nothing in the engine mutates the map during play, so every Game on
        this scenario can use one instance -- which also means the A* path
        cache warms across matches instead of being thrown away each game.
        Pass copy=True if you intend to modify terrain per-game.
        """
        if not copy and self._map_cache is not None:
            return self._map_cache
        m = HexMap(self.width, self.height)
        m.terrain = dict(self._terrain)
        m.roads = set(self._roads)
        m.finalize()
        if not copy:
            self._map_cache = m
        return m

    def build_units(self) -> List[Unit]:
        return [u.clone() for u in self._units]

    def home_edge(self, side: Side) -> int:
        return 0 if side is Side.BLUE else self.width - 1
