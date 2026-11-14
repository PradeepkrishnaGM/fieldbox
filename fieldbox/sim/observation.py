"""Observation: the only thing agents may read.

A fresh snapshot per side per turn. It holds frozen unit views, never live
`Unit` objects and never the `Game`. Enemy information is filtered by fog of
war: `enemy_units` holds only units inside the side's
visibility, `ghosts` the recently lost contacts, and `enemy_structures` the
structures seen so far (`age` 0 = visible now, larger = last seen that many
turns ago). The static world (map, provinces) is shared read-only.

The query methods mirror the old `Game` API (`units_of`, `unit_at`,
`enemies_adjacent`, `objectives`, ...) so agent code reads naturally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.hexgrid import Coord, Domain, HexMap, distance, neighbors
from . import combat as C
from .orders import Attack, Bombard, Build, Construct, Hold, MoveTo, Order, Repair, Strike
from .structures import STATS as SSTATS, StructureLike, StructureType
from .units import STATS as USTATS, Side, UnitLike, UnitType
from .. import params as P


@dataclass(frozen=True)
class UnitView(UnitLike):
    uid: int
    side: Side
    utype: UnitType
    q: int
    r: int
    strength: float
    entrench: int
    supply: float
    in_supply: bool
    order: Optional[Order] = None      # own units only: the standing order


@dataclass(frozen=True)
class GhostView(UnitLike):
    """A lost contact: last known type, position and strength, `age` turns old.
    Behaves like a unit for distance and domain queries; it cannot be ordered
    against."""
    uid: int
    side: Side
    utype: UnitType
    q: int
    r: int
    strength: float
    age: int
    entrench: int = 0
    supply: float = 100.0
    in_supply: bool = True
    order: Optional[Order] = None


@dataclass(frozen=True)
class StructureView(StructureLike):
    sid: int
    kind: StructureType
    side: Side
    q: int
    r: int
    strength: float
    age: int = 0                 # turns since last seen (enemy structures); 0 = visible now


class Observation:
    def __init__(self, side: Side, turn: int, max_turns: int, world,
                 own_units: Tuple[UnitView, ...], enemy_units: Tuple[UnitView, ...],
                 owner: Dict[int, Optional[Side]], vp: Dict[Side, float],
                 supply_net: Dict[Domain, frozenset], hq: Dict[Side, Coord],
                 own_structures: Tuple[StructureView, ...] = (),
                 enemy_structures: Tuple[StructureView, ...] = (),
                 pp: float = 0.0, cp: int = 0, power: float = 1.0,
                 queues: Optional[Dict[int, Tuple[Tuple[UnitType, int], ...]]] = None,
                 building: frozenset = frozenset(),
                 ghosts: Tuple[GhostView, ...] = (),
                 visible_land: frozenset = frozenset(),
                 visible_air: frozenset = frozenset()) -> None:
        self.side = side
        self.turn = turn
        self.max_turns = max_turns
        self.world = world                      # static, shared read-only
        self.map: HexMap = world.hexmap
        self.own_units = own_units
        self.enemy_units = enemy_units               # visible now, exact
        self.ghosts = ghosts                         # lost contacts, not exact
        self.visible_land = visible_land             # hexes where land units are seen
        self.visible_air = visible_air               # hexes where air and sea units are seen
        self.owner = owner
        self.vp = vp
        self.supply_net = {side: supply_net}    # own side only
        self.hq = hq
        self.own_structures = own_structures
        self.enemy_structures = enemy_structures      # seen so far (see `age`)
        self.pp = pp                                  # own production points
        self.cp = cp                                  # own command points this turn
        self.power = power                            # own power factor
        self.queues = queues or {}                    # own: structure id -> ((utype, turns_left), ...)
        self.building = building                      # hexes where an own engineer is building
        self._struct_at: Dict[Coord, StructureView] = {
            st.coord: st for st in own_structures + enemy_structures}
        self._by_coord: Dict[Coord, Dict[Domain, UnitView]] = {}
        for u in own_units + enemy_units:
            self._by_coord.setdefault(u.coord, {})[u.domain] = u
        self._capitals = {p.capital: p.id for p in world.provinces}
        # Side-relative order (own half first; RED's list mirrors BLUE's), so
        # an agent that picks by list index behaves the same on either side.
        rel = (lambda p: p.id) if side is Side.BLUE else (lambda p: world.mirror_province(p.id))
        self.objectives: Dict[Coord, int] = {
            p.capital: p.value for p in sorted(world.provinces, key=rel)}

    @classmethod
    def from_game(cls, game, side: Side) -> "Observation":
        def view(u, own: bool) -> UnitView:
            return UnitView(u.uid, u.side, u.utype, u.q, u.r, u.strength, u.entrench,
                            u.supply, u.in_supply, game.standing.get(u.uid) if own else None)
        own = tuple(view(u, True) for u in game.units_of(side))
        vis = game.vis[side]
        enemy = tuple(view(u, False) for u in game.units_of(side.other)
                      if vis.sees(u.domain, u.coord))
        mem = game.fog[side]
        ghosts = tuple(GhostView(g.uid, Side(g.side), g.utype, g.q, g.r, g.strength,
                                 game.turn - g.seen) for g in mem.ghosts(game.turn))
        nets = {d: frozenset(n) for d, n in game.supply_net.get(side, {}).items()}
        def sview(st) -> StructureView:
            return StructureView(st.sid, st.kind, st.side, st.q, st.r, st.strength)
        own_s = tuple(sview(st) for st in sorted(game.structures_of(side), key=lambda x: x.sid))
        enemy_s = tuple(StructureView(r.sid, r.kind, Side(r.side), r.q, r.r, r.strength,
                                      game.turn - r.seen)
                        for _, r in sorted(mem.structures.items()))
        queues = {st.sid: tuple((i.utype, i.left) for i in game.queues.get(st.sid, ()))
                  for st in game.structures_of(side)}
        building = frozenset(v[0] for uid, v in game.constructions.items()
                             if uid in game.units and game.units[uid].side is side)
        return cls(side, game.turn, game.max_turns, game.world, own, enemy,
                   dict(game.owner), dict(game.vp), nets, dict(game.hq),
                   own_s, enemy_s, game.pp[side], game.cp(side), game.power[side],
                   queues, building, ghosts, vis.land, vis.air)

    # -- queries mirroring the Game API -----------------------------------

    def units_of(self, side: Side) -> List[UnitView]:
        return list(self.own_units if side == self.side else self.enemy_units)

    def unit_at(self, coord: Coord, domain: Optional[Domain] = None) -> Optional[UnitView]:
        stack = self._by_coord.get(coord)
        if not stack:
            return None
        if domain is not None:
            return stack.get(domain)
        for d in (Domain.LAND, Domain.SEA, Domain.AIR):
            if d in stack:
                return stack[d]
        return None

    def units_at(self, coord: Coord) -> List[UnitView]:
        return list(self._by_coord.get(coord, {}).values())

    def occupied(self, domain: Domain, exclude: Optional[int] = None) -> frozenset:
        return frozenset(c for c, s in self._by_coord.items()
                         if domain in s and s[domain].uid != exclude)

    def enemies_adjacent(self, coord: Coord, side: Side,
                         domain: Optional[Domain] = None) -> List[UnitView]:
        out = []
        for n in neighbors(*coord):
            for d, u in self._by_coord.get(n, {}).items():
                if u.side != side and (domain is None or d is domain):
                    out.append(u)
        return out

    def structures_of(self, side: Side, kind: Optional[StructureType] = None) -> List[StructureView]:
        lst = self.own_structures if side == self.side else self.enemy_structures
        return [s for s in lst if kind is None or s.kind is kind]

    def structure_at(self, coord: Coord) -> Optional[StructureView]:
        return self._struct_at.get(coord)

    def build_options(self) -> List[Build]:
        """Every Build order that is legal and affordable right now."""
        out = []
        for st in self.own_structures:
            dom = st.stats.produces
            if dom is None or len(self.queues.get(st.sid, ())) >= P.ECONOMY.max_queue:
                continue
            for t in UnitType:
                us = USTATS[t]
                if us.domain is dom and us.cost <= self.pp:
                    out.append(Build(st.sid, t))
        return out

    def objective_holder(self, coord: Coord) -> Optional[Side]:
        pid = self._capitals.get(coord)
        return None if pid is None else self.owner.get(pid)

    # -- combat estimates (same formulas as the engine) -------------------

    def _supporting(self, attacker: UnitView, defender: UnitView) -> int:
        n_sup = 0
        for n in neighbors(*defender.coord):
            for u in self._by_coord.get(n, {}).values():
                if u.side == attacker.side and u.uid != attacker.uid and u.can_engage(defender):
                    n_sup += 1
        return n_sup

    def attack_power(self, attacker: UnitView, defender: UnitView) -> float:
        return C.attack_power(attacker, defender, self.map, self._supporting(attacker, defender))

    def defense_power(self, defender: UnitView, attacker: Optional[UnitView] = None) -> float:
        return C.defense_power(defender, self.map, attacker)

    def path_cost(self, start: Coord, goal: Coord, domain: Domain = Domain.LAND) -> Optional[float]:
        path = self.map.find_path(start, goal, None, domain)
        if path is None:
            return None
        return sum(self.map.move_cost(c, domain) for c in path)

    def legal_orders(self, unit: UnitView) -> List[Order]:
        orders: List[Order] = [Hold(unit.uid)]
        st = unit.stats
        for e in self.enemy_units:
            if not unit.can_engage(e):
                continue
            if st.indirect:
                if distance(unit.coord, e.coord) <= st.reach:
                    orders.append(Bombard(unit.uid, e.coord))
            else:
                orders.append(Attack(unit.uid, e.uid))
        for coord in self.objectives:
            if self.map.passable(coord, st.domain):
                orders.append(MoveTo(unit.uid, coord))
        for s in self.enemy_structures:
            if Domain.LAND not in st.targets:
                continue
            if st.domain is Domain.AIR:
                orders.append(Strike(unit.uid, s.sid))
            elif st.indirect:
                if distance(unit.coord, s.coord) <= st.reach:
                    orders.append(Bombard(unit.uid, s.coord))
            elif not any(e.coord == s.coord and e.domain is Domain.LAND for e in self.enemy_units):
                orders.append(Attack(unit.uid, s.sid, structure=True))
        if st.builds:
            for s in self.own_structures:
                if s.strength < 100.0 and distance(unit.coord, s.coord) <= 1:
                    orders.append(Repair(unit.uid, s.sid))
        return orders
