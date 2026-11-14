"""Game state and simultaneous (WEGO) turn resolution (PLAN.md §3.5).

Design notes
------------
* Both sides order, then everything resolves at once. `step_turn` takes
  `{Side: [orders]}` and runs the phases in a fixed order.
* Strictly deterministic. Each random stream is derived from the game seed
  and the turn (`make_rng(seed, "move", turn)` and so on), so no phase can
  disturb another's draws.
* No BLUE/RED bias: movement order is a seeded shuffle, all combat losses
  are computed from the same pre-combat state and applied together, and
  ties for an emptied hex are settled by another seeded shuffle.
* Stacking is per domain: a hex holds at most one land, one air and one sea
  unit.
* The engine contains no unit-type branches. Everything a unit may do comes
  from `UnitStats` -- domain, `targets`, `reach`, `can_capture`,
  `entrenches`.
* M3 scope: no structures, economy or command points. Supply sources are
  the start positions (land/air: HQ; sea: water next to the start port).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.hexgrid import IMPASSABLE, Coord, Domain, HexMap, Terrain, distance, neighbors
from ..core.rng import make_rng
from . import combat as C
from . import events as E
from .events import Event
from .orders import ORDER_TYPES, Attack, Bombard, Hold, MoveTo, Order
from .supply import (DRAIN_RATE, REPAIR_CAP, REPAIR_RATE, RESUPPLY_RATE,
                     STARVATION_LOSS, SUPPLY_EFFECTIVE, SUPPLY_MAX, interdicted,
                     trace_network)
from .units import MAX_ENTRENCH, Side, Unit, UnitType

# Re-exported so older imports (`from fieldbox.sim.engine import Hold`) work.
__all__ = ["Game", "CombatResult", "Hold", "MoveTo", "Attack", "Bombard", "Order"]

JITTER_LO, JITTER_SPAN = 0.80, 0.40      # combat jitter is uniform in [0.8, 1.2]


@dataclass
class CombatResult:
    attacker: int
    defender: int
    odds: float
    attacker_loss: float
    defender_loss: float
    defender_destroyed: bool


class Game:
    """One match on a generated world."""

    def __init__(self, world, seed: int = 0, max_turns: Optional[int] = None) -> None:
        self.world = world
        self.map: HexMap = world.hexmap
        self.seed = seed
        self.turn = 0
        self.max_turns = max_turns if max_turns is not None else world.params.match.max_turns
        # uids interleave the sides (BLUE 0, RED 1, BLUE 2, ...): world.units
        # lists BLUE's units, then their mirrors in the same order.
        self.units: Dict[int, Unit] = {}
        half = len(world.units) // 2
        for i, pu in enumerate(world.units):
            uid = 2 * i if i < half else 2 * (i - half) + 1
            self.units[uid] = Unit(uid, pu.side, UnitType[pu.kind], pu.coord[0], pu.coord[1])
        self.units = dict(sorted(self.units.items()))
        self._by_coord: Dict[Coord, Dict[Domain, Unit]] = {}
        for u in self.units.values():
            self._by_coord.setdefault(u.coord, {})[u.domain] = u
        self.owner = dict(world.owner)                     # province id -> Side | None
        self.capitals: Dict[Coord, int] = {p.capital: p.id for p in world.provinces}
        self.vp: Dict[Side, float] = {Side.BLUE: 0.0, Side.RED: 0.0}
        self.standing: Dict[int, Order] = {}
        self.events: List[Event] = []
        self.combat_log: List[CombatResult] = []
        self.result: Optional[Tuple[Optional[Side], str]] = None
        self.hq: Dict[Side, Coord] = dict(world.hq)
        self.supply_net: Dict[Side, Dict[Domain, set]] = {}
        self._depots = self._make_depots()
        self._static_net: Dict[tuple, set] = {}
        for s in (Side.BLUE, Side.RED):
            self._update_supply(s, emit=False)

    # -- queries ----------------------------------------------------------

    def units_of(self, side: Side) -> List[Unit]:
        return [u for u in self.units.values() if u.side == side and u.alive]

    def unit_at(self, coord: Coord, domain: Optional[Domain] = None) -> Optional[Unit]:
        """The unit in `domain` at `coord`; with no domain, the land unit if
        there is one, else whatever else is there."""
        stack = self._by_coord.get(coord)
        if not stack:
            return None
        if domain is not None:
            return stack.get(domain)
        for d in (Domain.LAND, Domain.SEA, Domain.AIR):
            if d in stack:
                return stack[d]
        return None

    def units_at(self, coord: Coord) -> List[Unit]:
        return list(self._by_coord.get(coord, {}).values())

    def occupied(self, domain: Domain, exclude: Optional[int] = None) -> frozenset:
        return frozenset(c for c, stack in self._by_coord.items()
                         if domain in stack and stack[domain].uid != exclude)

    def enemies_adjacent(self, coord: Coord, side: Side,
                         domain: Optional[Domain] = None) -> List[Unit]:
        out = []
        get = self._by_coord.get
        for n in neighbors(*coord):
            stack = get(n)
            if stack:
                for d, u in stack.items():
                    if u.side != side and (domain is None or d is domain):
                        out.append(u)
        return out

    @property
    def objectives(self) -> Dict[Coord, int]:
        """Province capitals and their values (what agents fight over)."""
        return {p.capital: p.value for p in self.world.provinces}

    def objective_holder(self, coord: Coord) -> Optional[Side]:
        """Owner of the province whose capital is `coord`."""
        pid = self.capitals.get(coord)
        return None if pid is None else self.owner.get(pid)

    @property
    def is_over(self) -> bool:
        return self.result is not None

    def winner(self) -> Optional[Side]:
        return self.result[0] if self.result else None

    def observe(self, side: Side):
        from .observation import Observation
        return Observation.from_game(self, side)

    def legal_orders(self, unit) -> List[Order]:
        orders: List[Order] = [Hold(unit.uid)]
        st = unit.stats
        for e in self.units.values():
            if e.side == unit.side or not unit.can_engage(e):
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

    # -- the turn ---------------------------------------------------------

    def step_turn(self, orders: Dict[Side, List[Order]]) -> List[Event]:
        """Resolve one simultaneous turn. Returns the events it produced."""
        if self.is_over:
            return []
        start = len(self.events)
        self._phase_orders(orders)
        self._phase_movement()
        self._phase_combat()
        self._phase_captures()
        self._phase_logistics()
        self._phase_score()
        self._emit(E.TURN_END)
        self.turn += 1
        return self.events[start:]

    def _emit(self, kind: str, side: Optional[Side] = None, **data) -> None:
        self.events.append(Event(self.turn, kind, None if side is None else int(side), data))

    # 1. orders ------------------------------------------------------------

    def _illegal(self, o, side: Side) -> Optional[str]:
        if not isinstance(o, ORDER_TYPES):
            return "unknown order type"
        u = self.units.get(o.uid)
        if u is None or u.side != side:
            return "no such unit"
        if isinstance(o, MoveTo):
            if not self.map.in_bounds(o.dest) or not self.map.passable(o.dest, u.domain):
                return "destination impassable"
        elif isinstance(o, Attack):
            t = self.units.get(o.target)
            if t is None or t.side == side:
                return "no such enemy"
            if not u.can_engage(t):
                return "cannot engage that domain"
        elif isinstance(o, Bombard):
            if not u.stats.indirect:
                return "not a ranged unit"
            if not self.map.in_bounds(o.dest):
                return "off the map"
        return None

    def _phase_orders(self, orders: Dict[Side, List[Order]]) -> None:
        for side in (Side.BLUE, Side.RED):
            seen = set()
            for o in orders.get(side, ()) or ():
                reason = self._illegal(o, side)
                if reason is None and o.uid in seen:
                    reason = "second order for the unit"
                if reason is not None:
                    self._emit(E.ORDER_REJECTED, side, uid=getattr(o, "uid", None),
                               order=repr(o), reason=reason)
                    continue
                seen.add(o.uid)
                self.standing[o.uid] = o

    # 2. movement ----------------------------------------------------------

    def _phase_movement(self) -> None:
        plans: Dict[int, List[Coord]] = {}
        for uid in sorted(self.standing):
            u = self.units.get(uid)
            o = self.standing[uid]
            if u is None:
                continue
            path = None
            if isinstance(o, MoveTo) and u.coord != o.dest:
                path = self.map.find_path(u.coord, o.dest,
                                          self.occupied(u.domain, exclude=uid), u.domain)
            elif isinstance(o, Attack):
                t = self.units.get(o.target)
                if t is not None and distance(u.coord, t.coord) > 1:
                    path = self.map.find_path(u.coord, t.coord,
                                              self.occupied(u.domain, exclude=uid), u.domain)
                    path = path[:-1] if path else None      # stop adjacent
            if path:
                plans[uid] = path
                u.moves_left = u.stats.move

        # Units that hold this turn dig in.
        for uid in sorted(self.standing):
            u = self.units.get(uid)
            if u is not None and isinstance(self.standing[uid], Hold) and u.stats.entrenches:
                u.entrench = min(MAX_ENTRENCH, u.entrench + 1)

        order = sorted(plans)
        make_rng(self.seed, "move", self.turn).shuffle(order)
        walked: Dict[int, List[Coord]] = {uid: [] for uid in order}
        start = {uid: self.units[uid].coord for uid in order}
        active = list(order)
        while active:                        # one step per unit per impulse
            still = []
            for uid in active:
                u = self.units[uid]
                path = plans[uid]
                step = path[len(walked[uid])]
                cost = self.map.move_cost(step, u.domain)
                if cost >= IMPASSABLE or cost > u.moves_left or \
                        self.unit_at(step, u.domain) is not None:
                    continue                 # refused: stops for the turn
                u.moves_left -= cost
                self._set_pos(u, step)
                u.entrench = 0
                walked[uid].append(step)
                if len(walked[uid]) == len(path):
                    continue
                if self.enemies_adjacent(step, u.side, u.domain):
                    continue                 # entered enemy ZOC: movement ends
                still.append(uid)
            active = still
        for uid in order:
            if walked[uid]:
                u = self.units[uid]
                self._emit(E.MOVED, u.side, uid=uid, path=[list(start[uid])] + [list(c) for c in walked[uid]])
        for uid, o in list(self.standing.items()):
            if isinstance(o, MoveTo) and uid in self.units and self.units[uid].coord == o.dest:
                self.standing[uid] = Hold(uid)

    # 3. combat ------------------------------------------------------------

    def _target_at(self, unit: Unit, coord: Coord) -> Optional[Unit]:
        stack = self._by_coord.get(coord, {})
        for d in (Domain.LAND, Domain.SEA, Domain.AIR):
            t = stack.get(d)
            if t is not None and t.side != unit.side and unit.can_engage(t):
                return t
        return None

    def _supporting(self, attacker: Unit, defender: Unit) -> int:
        n_sup = 0
        for n in neighbors(*defender.coord):
            for u in self._by_coord.get(n, {}).values():
                if u.side == attacker.side and u.uid != attacker.uid and u.can_engage(defender):
                    n_sup += 1
        return n_sup

    def _phase_combat(self) -> None:
        engagements: List[Tuple[Unit, Unit, str]] = []
        for uid in sorted(self.standing):
            u = self.units.get(uid)
            o = self.standing[uid]
            if u is None:
                continue
            if isinstance(o, Attack):
                t = self.units.get(o.target)
                if t is not None and distance(u.coord, t.coord) == 1 and u.can_engage(t):
                    engagements.append((u, t, "assault"))
            elif isinstance(o, Bombard) and distance(u.coord, o.dest) <= u.stats.reach:
                t = self._target_at(u, o.dest)
                if t is not None:
                    engagements.append((u, t, "fire"))
        # Air defence fires on its own when it has nothing else to do.
        for u in list(self.units.values()):
            st = u.stats
            o = self.standing.get(u.uid)
            if st.indirect and st.targets == frozenset({Domain.AIR}) and \
                    (o is None or isinstance(o, Hold)):
                air = [e for e in self.units.values() if e.side != u.side
                       and e.domain is Domain.AIR and distance(u.coord, e.coord) <= st.reach]
                if air:
                    engagements.append((u, max(air, key=lambda e: (e.strength, -e.uid)), "fire"))
        if not engagements:
            return

        rng = make_rng(self.seed, "combat", self.turn)
        loss: Dict[int, float] = {}
        cost: Dict[int, float] = {}
        assaults: List[Tuple[Unit, Unit]] = []
        for a, d, kind in engagements:       # all from pre-combat state
            if kind == "assault":
                ap = C.attack_power(a, d, self.map, self._supporting(a, d))
                dp = C.defense_power(d, self.map, a)
                odds = ap / dp
                jd = JITTER_LO + JITTER_SPAN * rng.random()
                ja = JITTER_LO + JITTER_SPAN * rng.random()
                dl, al = C.assault_losses(odds, jd, ja)
                assaults.append((a, d))
            else:
                odds = C.fire_power(a) / C.defense_power(d, self.map)
                dl, al = C.fire_loss(odds, JITTER_LO + JITTER_SPAN * rng.random()), 0.0
            loss[d.uid] = loss.get(d.uid, 0.0) + dl
            loss[a.uid] = loss.get(a.uid, 0.0) + al
            cost[a.uid] = cost.get(a.uid, 0.0) + a.stats.combat_cost
            self._emit(E.ENGAGED, a.side, attacker=a.uid, defender=d.uid, mode=kind,
                       odds=round(odds, 6), attacker_loss=round(al, 6), defender_loss=round(dl, 6))
            self.combat_log.append(CombatResult(a.uid, d.uid, odds, al, dl, False))
        for a, d, kind in engagements:
            if kind == "assault":
                a.entrench = 0
        for uid in sorted(loss):
            self.units[uid].strength -= loss[uid]
        for uid in sorted(cost):
            u = self.units[uid]
            u.supply = max(0.0, u.supply - cost[uid])
        dead = [u for u in self.units.values() if u.strength <= 0.0]
        dead_ids = {u.uid for u in dead}
        for cr in self.combat_log[-len(engagements):]:
            cr.defender_destroyed = cr.defender in dead_ids
        freed = {u.uid: u.coord for u in dead}
        for u in dead:
            self._kill(u, "combat")
        # Take the ground: a surviving assaulter may advance into the hex it
        # emptied. Competing claims are settled by a seeded shuffle.
        claims = [(a, freed[d.uid]) for a, d in assaults if d.uid in freed and a.uid not in dead_ids]
        claims.sort(key=lambda x: x[0].uid)
        make_rng(self.seed, "occupy", self.turn).shuffle(claims)
        for a, hex_ in claims:
            if self._can_occupy(a, hex_):
                old = a.coord
                self._set_pos(a, hex_)
                self._emit(E.MOVED, a.side, uid=a.uid, path=[list(old), list(hex_)], advance=True)
        # Orders against dead targets are finished.
        for uid, o in list(self.standing.items()):
            if uid not in self.units:
                del self.standing[uid]
            elif isinstance(o, Attack) and o.target not in self.units:
                del self.standing[uid]
            elif isinstance(o, Bombard) and self._target_at(self.units[uid], o.dest) is None:
                del self.standing[uid]

    def _can_occupy(self, unit: Unit, coord: Coord) -> bool:
        if not self.map.passable(coord, unit.domain):
            return False
        stack = self._by_coord.get(coord)
        return not (stack and unit.domain in stack)

    # 4. captures ----------------------------------------------------------

    def _phase_captures(self) -> None:
        for p in self.world.provinces:
            holder = self.unit_at(p.capital, Domain.LAND)
            if holder is not None and holder.stats.can_capture and self.owner[p.id] != holder.side:
                prev = self.owner[p.id]
                self.owner[p.id] = holder.side
                self._emit(E.PROVINCE_FLIPPED, holder.side, province=p.id,
                           previous=None if prev is None else int(prev))

    # 5. logistics ---------------------------------------------------------

    def _make_depots(self) -> Dict[Tuple[Side, Domain], List[Coord]]:
        t = self.map.terrain
        out = {}
        for side in (Side.BLUE, Side.RED):
            hq = self.hq[side]
            ports = [s.coord for s in self.world.structures if s.side is side and s.kind == "PORT"]
            sea = sorted({n for p in ports for n in neighbors(*p) if t.get(n) is Terrain.WATER})
            out[(side, Domain.LAND)] = [hq]
            out[(side, Domain.AIR)] = [hq]
            out[(side, Domain.SEA)] = sea
        return out

    def _update_supply(self, side: Side, emit: bool = True) -> None:
        nets: Dict[Domain, set] = {}
        for domain in Domain:
            if domain is Domain.LAND:
                nets[domain] = trace_network(self.map, self._depots[(side, domain)], domain,
                                             interdicted(self, side))
                continue
            key = (side, domain)
            if key not in self._static_net:     # air/sea are not interdicted by ground
                self._static_net[key] = trace_network(self.map, self._depots[key], domain)
            nets[domain] = self._static_net[key]
        self.supply_net[side] = nets
        for u in self.units_of(side):
            was = u.in_supply
            u.in_supply = u.coord in nets[u.domain]
            if emit and was and not u.in_supply:
                self._emit(E.SUPPLY_CUT, side, uid=u.uid)

    def _phase_logistics(self) -> None:
        for side in (Side.BLUE, Side.RED):
            self._update_supply(side)
        for side in (Side.BLUE, Side.RED):
            for u in self.units_of(side):
                st = u.stats
                if u.in_supply:
                    u.supply = min(SUPPLY_MAX, u.supply + RESUPPLY_RATE - st.upkeep)
                    if u.supply > SUPPLY_EFFECTIVE and not self.enemies_adjacent(u.coord, side):
                        u.strength = min(REPAIR_CAP, u.strength + REPAIR_RATE)
                else:
                    u.supply -= DRAIN_RATE + st.upkeep
                    if u.supply <= 0.0:
                        u.supply = 0.0
                        u.strength -= STARVATION_LOSS
                        if u.strength <= 0.0:
                            self._kill(u, "starvation")

    # 7. scoring and victory -------------------------------------------------

    def _phase_score(self) -> None:
        for p in self.world.provinces:
            own = self.owner.get(p.id)
            if own is not None:
                self.vp[own] += p.value
        blue, red = self.units_of(Side.BLUE), self.units_of(Side.RED)
        if not blue or not red:
            winner = Side.BLUE if blue else Side.RED if red else None
            self.result = (winner, "annihilation")
        elif self.turn + 1 >= self.max_turns:
            b, r = self.vp[Side.BLUE], self.vp[Side.RED]
            self.result = (Side.BLUE if b > r else Side.RED if r > b else None, "turn_limit")
        if self.result is not None:
            w = self.result[0]
            self._emit(E.GAME_OVER, w, winner=None if w is None else w.name, cause=self.result[1],
                       vp_blue=self.vp[Side.BLUE], vp_red=self.vp[Side.RED])

    # -- position bookkeeping -------------------------------------------------

    def _set_pos(self, unit: Unit, coord: Coord) -> None:
        old = self._by_coord.get(unit.coord)
        if old is not None and old.get(unit.domain) is unit:
            del old[unit.domain]
            if not old:
                del self._by_coord[unit.coord]
        unit.q, unit.r = coord
        self._by_coord.setdefault(coord, {})[unit.domain] = unit

    def place(self, unit: Unit, coord: Coord) -> None:
        """Move a unit and keep the position index in sync (tests, tools)."""
        self._set_pos(unit, coord)

    def _kill(self, unit: Unit, cause: str) -> None:
        unit.strength = 0.0
        stack = self._by_coord.get(unit.coord)
        if stack is not None and stack.get(unit.domain) is unit:
            del stack[unit.domain]
            if not stack:
                del self._by_coord[unit.coord]
        self.units.pop(unit.uid, None)
        self.standing.pop(unit.uid, None)
        self._emit(E.UNIT_DESTROYED, unit.side, uid=unit.uid, utype=unit.utype.name, cause=cause)
