"""Observation: the only thing agents may read (PLAN.md §4.1).

A fresh snapshot per side per turn. It holds frozen unit views, never live
`Unit` objects and never the `Game`. Enemy information is available only
through `enemy_units`, so fog of war (M6) needs to filter exactly one list.
The static world (map, provinces) is shared read-only.

The query methods mirror the old `Game` API (`units_of`, `unit_at`,
`enemies_adjacent`, `objectives`, ...) so agent code reads naturally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.hexgrid import Coord, Domain, HexMap, distance, neighbors
from . import combat as C
from .orders import Attack, Bombard, Hold, MoveTo, Order
from .units import Side, UnitLike, UnitType


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


class Observation:
    def __init__(self, side: Side, turn: int, max_turns: int, world,
                 own_units: Tuple[UnitView, ...], enemy_units: Tuple[UnitView, ...],
                 owner: Dict[int, Optional[Side]], vp: Dict[Side, float],
                 supply_net: Dict[Domain, frozenset], hq: Dict[Side, Coord]) -> None:
        self.side = side
        self.turn = turn
        self.max_turns = max_turns
        self.world = world                      # static, shared read-only
        self.map: HexMap = world.hexmap
        self.own_units = own_units
        self.enemy_units = enemy_units
        self.owner = owner
        self.vp = vp
        self.supply_net = {side: supply_net}    # own side only
        self.hq = hq
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
        enemy = tuple(view(u, False) for u in game.units_of(side.other))
        nets = {d: frozenset(n) for d, n in game.supply_net.get(side, {}).items()}
        return cls(side, game.turn, game.max_turns, game.world, own, enemy,
                   dict(game.owner), dict(game.vp), nets, dict(game.hq))

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
        return orders
