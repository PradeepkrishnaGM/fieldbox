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
* M4 adds structures, the economy (PP, power, production queues), command
  points, FOB construction and structure-based supply and victory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.hexgrid import IMPASSABLE, Coord, Domain, HexMap, Terrain, distance, neighbors
from ..core.rng import make_rng
from . import combat as C
from . import events as E
from .events import Event
from .. import params as P
from .orders import (ORDER_TYPES, Attack, Bombard, Build, Construct, Hold, MoveTo,
                     Order, Repair, Strike)
from .structures import STATS as SSTATS
from .structures import STRUCTURE_VP, Structure, StructureType as ST
from .supply import (AIR_REARM_RADIUS, DRAIN_RATE, FOB_BUDGET, HQ_BUDGET, PORT_BUDGET,
                     REPAIR_CAP, REPAIR_RATE, RESUPPLY_RATE, STARVATION_LOSS,
                     SUPPLY_EFFECTIVE, SUPPLY_MAX, interdicted, trace_network)
from .units import MAX_ENTRENCH, STATS as USTATS, Side, Unit, UnitType

ECO = P.ECONOMY
MIN_UNIT_COST = min(st.cost for st in USTATS.values() if st.cost > 0)

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


@dataclass
class QueueItem:
    utype: UnitType
    left: int


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
        # --- M4 state
        self.structures: Dict[int, Structure] = {}
        for i, ps in enumerate(world.structures):
            self.structures[i] = Structure(i, ST[ps.kind], ps.side, ps.coord[0], ps.coord[1])
        self._struct_at: Dict[Coord, Structure] = {s.coord: s for s in self.structures.values()}
        self._next_sid = len(world.structures)
        self._next_uid = max(self.units, default=-1) + 1
        self.pp: Dict[Side, float] = {s: float(world.params.start.start_pp) for s in Side}
        self.queues: Dict[int, List[QueueItem]] = {}
        self.constructions: Dict[int, list] = {}      # engineer uid -> [coord, kind, turns_left]
        self.power: Dict[Side, float] = {s: 1.0 for s in Side}
        self._attacked: set = set()                   # structure ids hit this turn
        self._static_net: Dict[tuple, set] = {}
        for s in Side:
            self.power[s] = self._power_factor(s)
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

    # -- structures, power, command ---------------------------------------

    def structures_of(self, side: Side, kind: Optional[ST] = None) -> List[Structure]:
        return [s for s in self.structures.values()
                if s.side is side and (kind is None or s.kind is kind)]

    def structure_at(self, coord: Coord) -> Optional[Structure]:
        return self._struct_at.get(coord)

    def _power_factor(self, side: Side) -> float:
        supply = sum(ECO.power_output * s.strength / 100.0
                     for s in self.structures_of(side, ST.POWER))
        demand = sum(s.stats.power_demand for s in self.structures_of(side))
        return 1.0 if demand <= 0 else max(0.0, min(1.0, supply / demand))

    def cp(self, side: Side) -> int:
        dc = min(1.0, sum(s.strength for s in self.structures_of(side, ST.DATACENTER)) / 100.0)
        return ECO.cp_base + int(ECO.cp_datacenter * dc * self.power[side])

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
        self._phase_economy()
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
        elif isinstance(o, Attack) and o.structure:
            s = self.structures.get(o.target)
            if s is None or s.side is side:
                return "no such enemy structure"
            if u.stats.indirect or u.domain is Domain.AIR or Domain.LAND not in u.stats.targets:
                return "cannot assault a structure"
            if self.enemy_land_at(s.coord, side) is not None:
                return "structure is defended"
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
        elif isinstance(o, Strike):
            s = self.structures.get(o.sid)
            if s is None or s.side is side:
                return "no such enemy structure"
            if u.domain is not Domain.AIR or Domain.LAND not in u.stats.targets:
                return "not a strike aircraft"
        elif isinstance(o, Construct):
            if not u.stats.builds:
                return "cannot build"
            if o.kind is not ST.FOB:
                return "only FOBs can be built"
            if not self.map.in_bounds(o.coord) or distance(u.coord, o.coord) > 1 \
                    or not self.map.passable(o.coord, Domain.LAND):
                return "site out of reach or impassable"
            if o.coord in self._struct_at:
                return "site already has a structure"
        elif isinstance(o, Repair):
            s = self.structures.get(o.sid)
            if not u.stats.builds:
                return "cannot repair"
            if s is None or s.side is not side or distance(u.coord, s.coord) > 1:
                return "no friendly structure in reach"
        return None

    def enemy_land_at(self, coord: Coord, side: Side) -> Optional[Unit]:
        u = self.unit_at(coord, Domain.LAND)
        return u if u is not None and u.side != side else None

    def _reject(self, side: Side, o, reason: str) -> None:
        self._emit(E.ORDER_REJECTED, side, uid=getattr(o, "uid", getattr(o, "sid", None)),
                   order=repr(o), reason=reason)

    def _do_build(self, o: Build, side: Side) -> None:
        s = self.structures.get(o.sid)
        if s is None or s.side is not side:
            return self._reject(side, o, "no such structure")
        dom = s.stats.produces
        ust = USTATS.get(o.utype)
        if dom is None or ust is None or ust.domain is not dom:
            return self._reject(side, o, "structure cannot build that")
        q = self.queues.setdefault(s.sid, [])
        if len(q) >= ECO.max_queue:
            return self._reject(side, o, "queue full")
        if self.pp[side] < ust.cost:
            return self._reject(side, o, "not enough PP")
        self.pp[side] -= ust.cost
        q.append(QueueItem(o.utype, ust.build_turns))
        self._emit(E.BUILD_QUEUED, side, sid=s.sid, utype=o.utype.name)

    def _phase_orders(self, orders: Dict[Side, List[Order]]) -> None:
        self._attacked = set()
        for side in (Side.BLUE, Side.RED):
            seen = set()
            cp, used, dropped = self.cp(side), 0, 0
            for o in orders.get(side, ()) or ():
                if isinstance(o, Build):
                    self._do_build(o, side)
                    continue
                reason = self._illegal(o, side)
                if reason is None and o.uid in seen:
                    reason = "second order for the unit"
                if reason is not None:
                    self._reject(side, o, reason)
                    continue
                seen.add(o.uid)
                if self.standing.get(o.uid) == o:
                    continue                  # re-issued as is: changes nothing, costs no CP
                if used >= cp:
                    dropped += 1
                    continue
                if isinstance(o, Construct) and self.pp[side] < ECO.fob_cost:
                    self._reject(side, o, "not enough PP")
                    continue
                used += 1
                self.constructions.pop(o.uid, None)    # a new order abandons a build site
                if isinstance(o, Construct):
                    self.pp[side] -= ECO.fob_cost
                    self.constructions[o.uid] = [o.coord, o.kind, ECO.fob_build_turns]
                    self._emit(E.FOB_STARTED, side, uid=o.uid, coord=list(o.coord))
                self.standing[o.uid] = o
            if dropped:
                self._emit(E.ORDERS_CAPPED, side, dropped=dropped, cp=cp)

    # 2. movement ----------------------------------------------------------

    def _target_coord(self, o) -> Optional[Coord]:
        if isinstance(o, Strike) or (isinstance(o, Attack) and o.structure):
            sid = o.sid if isinstance(o, Strike) else o.target
            st = self.structures.get(sid)
            return None if st is None else st.coord
        t = self.units.get(o.target)
        return None if t is None else t.coord

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
            elif isinstance(o, (Attack, Strike)):
                tc = self._target_coord(o)
                if tc is not None and distance(u.coord, tc) > 1:
                    path = self.map.find_path(u.coord, tc,
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
        s_eng: List[Tuple[Unit, Structure, str]] = []
        for uid in sorted(self.standing):
            u = self.units.get(uid)
            o = self.standing[uid]
            if u is None:
                continue
            if isinstance(o, Attack) and not o.structure:
                t = self.units.get(o.target)
                if t is not None and distance(u.coord, t.coord) == 1 and u.can_engage(t):
                    engagements.append((u, t, "assault"))
            elif isinstance(o, (Strike, Attack)):
                st = self.structures.get(o.sid if isinstance(o, Strike) else o.target)
                if st is not None and distance(u.coord, st.coord) == 1 \
                        and self.enemy_land_at(st.coord, u.side) is None:
                    s_eng.append((u, st, "assault"))
            elif isinstance(o, Bombard) and distance(u.coord, o.dest) <= u.stats.reach:
                t = self._target_at(u, o.dest)
                if t is not None:
                    engagements.append((u, t, "fire"))
                else:
                    st = self._struct_at.get(o.dest)
                    if st is not None and st.side is not u.side and Domain.LAND in u.stats.targets:
                        s_eng.append((u, st, "fire"))
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
        if not engagements and not s_eng:
            return

        rng = make_rng(self.seed, "combat", self.turn)
        loss: Dict[int, float] = {}
        cost: Dict[int, float] = {}
        sloss: Dict[int, float] = {}
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
        for a, st, kind in s_eng:            # no return fire from a structure
            ap = (C.attack_power(a, st, self.map, 0) if kind == "assault" else C.fire_power(a))
            odds = ap / C.structure_defense(st, self.map)
            dl = C.fire_loss(odds, JITTER_LO + JITTER_SPAN * rng.random())
            sloss[st.sid] = sloss.get(st.sid, 0.0) + dl
            cost[a.uid] = cost.get(a.uid, 0.0) + a.stats.combat_cost
            self._emit(E.STRUCTURE_DAMAGED, a.side, attacker=a.uid, sid=st.sid, mode=kind,
                       odds=round(odds, 6), loss=round(dl, 6))
        for a, d, kind in engagements:
            if kind == "assault":
                a.entrench = 0
        for a, st, kind in s_eng:
            if kind == "assault":
                a.entrench = 0
        for uid in sorted(loss):
            self.units[uid].strength -= loss[uid]
        for sid in sorted(sloss):
            st = self.structures[sid]
            st.strength -= sloss[sid]
            self._attacked.add(sid)
            if st.strength <= 0.0:
                self._destroy_structure(st)
        for uid in sorted(cost):
            u = self.units[uid]
            u.supply = max(0.0, u.supply - cost[uid])
        dead = [u for u in self.units.values() if u.strength <= 0.0]
        dead_ids = {u.uid for u in dead}
        for cr in self.combat_log[len(self.combat_log) - len(engagements):]:
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
            elif isinstance(o, Attack) and not o.structure and o.target not in self.units:
                del self.standing[uid]
            elif isinstance(o, Attack) and o.structure and o.target not in self.structures:
                del self.standing[uid]
            elif isinstance(o, Strike) and o.sid not in self.structures:
                del self.standing[uid]
            elif isinstance(o, Bombard) and self._target_at(self.units[uid], o.dest) is None \
                    and not (o.dest in self._struct_at and self._struct_at[o.dest].side is not self.units[uid].side):
                del self.standing[uid]

    def _destroy_structure(self, st: Structure) -> None:
        self.structures.pop(st.sid, None)
        if self._struct_at.get(st.coord) is st:
            del self._struct_at[st.coord]
        self.queues.pop(st.sid, None)
        vp = STRUCTURE_VP[st.kind]
        self.vp[st.side.other] += vp
        self._emit(E.STRUCTURE_DESTROYED, st.side, sid=st.sid, structure=st.kind.name,
                   coord=list(st.coord), vp=vp)

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
        for sid in sorted(self.structures):
            st = self.structures[sid]
            if not st.stats.capturable:
                continue
            u = self.unit_at(st.coord, Domain.LAND)
            if u is not None and u.stats.can_capture and u.side is not st.side:
                prev = st.side
                st.side = u.side
                st.strength = min(st.strength, ECO.capture_strength_cap)
                self.queues.pop(sid, None)
                self._emit(E.STRUCTURE_CAPTURED, u.side, sid=sid, structure=st.kind.name,
                           previous=int(prev))

    # 5. logistics ---------------------------------------------------------

    def _ring(self, center: Coord, d: int) -> List[Coord]:
        out = []
        for dq in range(-d, d + 1):
            for dr in range(-d, d + 1):
                c = (center[0] + dq, center[1] + dr)
                if self.map.in_bounds(c) and distance(c, center) == d:
                    out.append(c)
        return sorted(out)

    def _update_supply(self, side: Side, emit: bool = True) -> None:
        t = self.map.terrain
        hq = [s.coord for s in self.structures_of(side, ST.HQ)]
        fob = [s.coord for s in self.structures_of(side, ST.FOB)]
        blocked = interdicted(self, side)
        land = (trace_network(self.map, hq, Domain.LAND, blocked, HQ_BUDGET)
                | trace_network(self.map, fob, Domain.LAND, blocked, FOB_BUDGET))
        # Air: a radius around airfields and FOBs (not a trace); static per source set.
        air_src = tuple(sorted(s.coord for s in self.structures_of(side)
                               if s.kind in (ST.AIRFIELD, ST.FOB)))
        key = (side, "air", air_src)
        if key not in self._static_net:
            self._static_net[key] = {c for src in air_src for d in range(AIR_REARM_RADIUS + 1)
                                     for c in self._ring(src, d)}
        sea_src = tuple(sorted({n for s in self.structures_of(side, ST.PORT)
                                for n in neighbors(*s.coord) if t.get(n) is Terrain.WATER}))
        key2 = (side, "sea", sea_src)
        if key2 not in self._static_net:
            self._static_net[key2] = trace_network(self.map, list(sea_src), Domain.SEA, None, PORT_BUDGET)
        nets = {Domain.LAND: land, Domain.AIR: self._static_net[key],
                Domain.SEA: self._static_net[key2]}
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

    # 6. economy -----------------------------------------------------------

    def _region_bonus(self, side: Side) -> int:
        total = 0
        for rg in self.world.regions:
            if all(self.owner.get(pid) is side for pid in rg.provinces):
                total += rg.bonus
        return total

    def _phase_economy(self) -> None:
        for side in Side:
            old = self.power[side]
            pf = self.power[side] = self._power_factor(side)
            if pf < 1.0 and (pf < old or old >= 1.0):
                self._emit(E.POWER_LOW, side, factor=round(pf, 4))
            hq = sum(st.strength for st in self.structures_of(side, ST.HQ)) / 100.0
            income = ECO.hq_income * min(1.0, hq)
            income += sum(ECO.industry_income * st.strength / 100.0 * pf
                          for st in self.structures_of(side, ST.INDUSTRY))
            income += sum(p.value for p in self.world.provinces if self.owner.get(p.id) is side)
            income += self._region_bonus(side)
            self.pp[side] += income
        # Production: the head of each queue advances; finished units spawn
        # on the nearest free hex (contention is settled by a seeded shuffle).
        order = sorted(self.queues)
        make_rng(self.seed, "spawn", self.turn).shuffle(order)
        for sid in order:
            st = self.structures.get(sid)
            q = self.queues.get(sid)
            if st is None or not q:
                continue
            item = q[0]
            if item.left > 0:
                item.left -= 1
            if item.left == 0:
                site = self._spawn_site(st, USTATS[item.utype].domain)
                if site is not None:
                    q.pop(0)
                    u = Unit(self._next_uid, st.side, item.utype, site[0], site[1])
                    self._next_uid += 1
                    self.units[u.uid] = u
                    self._by_coord.setdefault(u.coord, {})[u.domain] = u
                    u.in_supply = u.coord in self.supply_net.get(u.side, {}).get(u.domain, ())
                    self._emit(E.UNIT_BUILT, st.side, uid=u.uid, utype=item.utype.name,
                               sid=sid, coord=list(site))
        # FOB construction and engineer repairs.
        for uid in sorted(self.constructions):
            eng = self.units.get(uid)
            site, kind, left = self.constructions[uid]
            if eng is None or distance(eng.coord, site) > 1 or site in self._struct_at:
                del self.constructions[uid]
                continue
            self.constructions[uid][2] = left = left - 1
            if left <= 0:
                st = Structure(self._next_sid, kind, eng.side, site[0], site[1])
                self._next_sid += 1
                self.structures[st.sid] = st
                self._struct_at[site] = st
                del self.constructions[uid]
                self.standing[uid] = Hold(uid)
                self._emit(E.FOB_BUILT, eng.side, sid=st.sid, coord=list(site))
        repaired = set()
        for uid in sorted(self.standing):
            o = self.standing[uid]
            if not isinstance(o, Repair):
                continue
            eng, st = self.units.get(uid), self.structures.get(o.sid)
            if eng is None or st is None or st.side is not eng.side or distance(eng.coord, st.coord) > 1:
                del self.standing[uid]
                continue
            st.strength = min(100.0, st.strength + ECO.engineer_repair_rate)
            repaired.add(st.sid)
            if st.strength >= 100.0:
                self.standing[uid] = Hold(uid)
        for st in sorted(self.structures.values(), key=lambda x: x.sid):
            if st.sid in self._attacked or st.strength >= 100.0:
                continue
            if st.coord in self.supply_net.get(st.side, {}).get(Domain.LAND, ()):
                st.strength = min(100.0, st.strength + ECO.structure_repair_rate)
        for s in Side:
            self._update_supply(s, emit=False)

    def _spawn_site(self, st: Structure, domain: Domain, max_d: int = 6) -> Optional[Coord]:
        # Ties between equally near hexes are broken in the owner's frame
        # (RED's coordinates mirrored), so neither side's reinforcements
        # drift toward one map corner.
        key = (lambda c: c) if st.side is Side.BLUE else self.world.mirror
        for d in range(max_d + 1):
            for c in sorted(self._ring(st.coord, d), key=key):
                if self.map.passable(c, domain) and self.unit_at(c, domain) is None:
                    return c
        return None

    # 7. scoring and victory -------------------------------------------------

    def _can_fight(self, side: Side) -> bool:
        if self.units_of(side):
            return True
        if any(self.queues.get(st.sid) for st in self.structures_of(side)):
            return True
        return self.pp[side] >= MIN_UNIT_COST and any(
            st.stats.produces is not None for st in self.structures_of(side))

    def _phase_score(self) -> None:
        for p in self.world.provinces:
            own = self.owner.get(p.id)
            if own is not None:
                self.vp[own] += p.value
        no_hq = [s for s in Side if not self.structures_of(s, ST.HQ)]
        if no_hq:
            winner = None if len(no_hq) == 2 else no_hq[0].other
            self.result = (winner, "hq_destroyed")
        else:
            blue, red = self._can_fight(Side.BLUE), self._can_fight(Side.RED)
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
