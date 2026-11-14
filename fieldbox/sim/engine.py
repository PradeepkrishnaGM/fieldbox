"""Game state and turn resolution.

Design notes
------------
* Strictly deterministic: every random draw comes from `self.rng`, seeded once.
  Same seed + same policies => byte-identical game.
* IGOUGO turn order (BLUE acts, then RED). One order per unit per turn, which
  keeps the action space small enough for RL to have a chance.
* `Attack` implies move-to-contact, so agents issue intent, not micro.
* **Stacking is per domain.** A hex holds at most one land unit, one air unit
  and one sea unit. Without this, a helicopter parked on a hex would block a
  tank, and air power would just be fast ground power.
* The engine contains no unit-type branches. Everything a unit may do comes
  from `UnitStats` -- domain, `targets`, `can_capture`, `entrenches`. Adding
  a unit type must never mean editing this file.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .. import params as P
from ..core.hexgrid import Coord, Domain, HexMap, distance, neighbors
from .supply import (
    DRAIN_RATE,
    REPAIR_CAP,
    REPAIR_RATE,
    RESUPPLY_RATE,
    STARVATION_LOSS,
    SUPPLY_EFFECTIVE,
    SUPPLY_MAX,
    depot_hexes,
    interdicted,
    supply_factor,
    trace_network,
)
from .units import (
    ATTACK_TERRAIN_MOD,
    ENTRENCH_BONUS,
    MAX_ENTRENCH,
    NO_RETURN_FIRE_MULT,
    Side,
    Unit,
)

# --- Orders ---------------------------------------------------------------


@dataclass(frozen=True)
class Hold:
    uid: int


@dataclass(frozen=True)
class MoveTo:
    uid: int
    dest: Coord


@dataclass(frozen=True)
class Attack:
    """Close with `target` (pathing to an adjacent hex) and engage."""
    uid: int
    target: int


@dataclass(frozen=True)
class Bombard:
    """Engage at range. No return fire, but only within `reach`."""
    uid: int
    dest: Coord


Order = object  # one of the four above

# --- Combat tuning --------------------------------------------------------

BASE_LOSS = P.COMBAT.base_loss      # percentage points lost at even odds
MIN_LOSS = P.COMBAT.min_loss
MAX_LOSS = P.COMBAT.max_loss
FLANK_BONUS = P.COMBAT.flank_bonus  # per additional friendly unit adjacent to the target
# Per-unit ranged effectiveness now lives in UnitStats.fire_coef.


@dataclass
class CombatResult:
    attacker: int
    defender: int
    odds: float
    attacker_loss: float
    defender_loss: float
    defender_destroyed: bool


class Game:
    """A single match. Cheap to construct, cheap to step."""

    def __init__(self, scenario, seed: int = 0) -> None:
        self.scenario = scenario
        self.map: HexMap = scenario.build_map()
        self.rng = random.Random(seed)
        self.seed = seed
        self.turn = 0
        self.max_turns = scenario.max_turns
        self.current_side = Side.BLUE
        self.objectives: Dict[Coord, int] = dict(scenario.objectives)
        self.units: Dict[int, Unit] = {u.uid: u.clone() for u in scenario.build_units()}
        # (coord -> domain -> unit). unit_at() runs thousands of times a turn,
        # so this index is what keeps the step cost flat.
        self._by_coord: Dict[Coord, Dict[Domain, Unit]] = {}
        for u in self.units.values():
            self._by_coord.setdefault(u.coord, {})[u.domain] = u
        self.vp: Dict[Side, float] = {Side.BLUE: 0.0, Side.RED: 0.0}
        self.log: List[str] = []
        self.combat_log: List[CombatResult] = []
        # coord sets that each side can currently trace supply to, per domain
        self.supply_net: Dict[Side, Dict[Domain, set]] = {}
        self._depot_cache: Dict[tuple, List[Coord]] = {}
        # Air and sea supply is not interdicted by ground, so those networks
        # depend only on the static map and never change during a game.
        # Retracing them every turn was two thirds of the supply cost.
        self._static_net: Dict[tuple, set] = {}
        for s in (Side.BLUE, Side.RED):
            self._update_supply(s)
        self._refresh_moves(self.current_side)

    # -- helpers ---------------------------------------------------------

    def units_of(self, side: Side) -> List[Unit]:
        return [u for u in self.units.values() if u.side == side and u.alive]

    def unit_at(self, coord: Coord, domain: Optional[Domain] = None) -> Optional[Unit]:
        """The unit in `domain` at `coord`.

        With no domain, returns the land unit if there is one -- ground truth
        for "who holds this hex" -- falling back to whatever else is there.
        """
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

    def occupied(
        self, domain: Domain, exclude: Optional[int] = None
    ) -> frozenset:
        """Hexes a unit of `domain` may not enter."""
        return frozenset(
            c for c, stack in self._by_coord.items()
            if domain in stack and stack[domain].uid != exclude
        )

    def enemies_adjacent(
        self, coord: Coord, side: Side, domain: Optional[Domain] = None
    ) -> List[Unit]:
        """Adjacent enemies, optionally restricted to one domain.

        Zone of control uses the domain filter: ground troops cannot stop a
        helicopter, and nothing on land exerts ZOC over open water.
        """
        out = []
        get = self._by_coord.get
        for n in neighbors(*coord):
            stack = get(n)
            if not stack:
                continue
            for d, u in stack.items():
                if u.side != side and (domain is None or d is domain):
                    out.append(u)
        return out

    @property
    def is_over(self) -> bool:
        if self.turn >= self.max_turns:
            return True
        return not self.units_of(Side.BLUE) or not self.units_of(Side.RED)

    def winner(self) -> Optional[Side]:
        if not self.is_over:
            return None
        blue, red = self.units_of(Side.BLUE), self.units_of(Side.RED)
        if not blue and red:
            return Side.RED
        if not red and blue:
            return Side.BLUE
        if self.vp[Side.BLUE] > self.vp[Side.RED]:
            return Side.BLUE
        if self.vp[Side.RED] > self.vp[Side.BLUE]:
            return Side.RED
        return None  # draw

    # -- legal actions (for RL wrappers and sanity checks) ----------------

    def legal_orders(self, unit: Unit) -> List[Order]:
        orders: List[Order] = [Hold(unit.uid)]
        st = unit.stats
        enemies = [u for u in self.units.values() if u.alive and u.side != unit.side]
        engageable = [e for e in enemies if e.domain in st.targets]
        if st.indirect:
            for e in engageable:
                if distance(unit.coord, e.coord) <= st.reach:
                    orders.append(Bombard(unit.uid, e.coord))
        else:
            for e in engageable:
                orders.append(Attack(unit.uid, e.uid))
        for coord in self.objectives:
            if self.map.passable(coord, st.domain):
                orders.append(MoveTo(unit.uid, coord))
        return orders

    # -- turn resolution --------------------------------------------------

    # -- logistics --------------------------------------------------------

    def _depots(self, side: Side, domain: Domain) -> List[Coord]:
        key = (side, domain)
        cached = self._depot_cache.get(key)
        if cached is None:
            home = 0 if side is Side.BLUE else self.map.width - 1
            cached = depot_hexes(self.map, home, domain)
            self._depot_cache[key] = cached
        return cached

    def _update_supply(self, side: Side) -> None:
        """Retrace the supply network and flag every unit in or out of it.

        Once per side per turn, never per unit -- per-unit tracing is what
        makes supply models slow.
        """
        nets: Dict[Domain, set] = {}
        for domain in Domain:
            if domain is Domain.LAND:
                nets[domain] = trace_network(
                    self.map, self._depots(side, domain), domain,
                    interdicted(self, side),
                )
                continue
            key = (side, domain)
            cached = self._static_net.get(key)
            if cached is None:
                cached = trace_network(
                    self.map, self._depots(side, domain), domain,
                )
                self._static_net[key] = cached
            nets[domain] = cached
        self.supply_net[side] = nets
        for u in self.units_of(side):
            u.in_supply = u.coord in nets[u.domain]

    def _run_logistics(self, side: Side) -> None:
        """End-of-turn upkeep: replenish, drain, starve, repair."""
        for u in list(self.units_of(side)):
            st = u.stats
            if u.in_supply:
                u.supply = min(SUPPLY_MAX, u.supply + RESUPPLY_RATE - st.upkeep)
                # Units in contact are busy fighting, not refitting.
                if u.supply > SUPPLY_EFFECTIVE and \
                        not self.enemies_adjacent(u.coord, side):
                    u.strength = min(REPAIR_CAP, u.strength + REPAIR_RATE)
            else:
                u.supply -= DRAIN_RATE + st.upkeep
                if u.supply <= 0.0:
                    u.supply = 0.0
                    u.strength -= STARVATION_LOSS
                    if u.strength <= 0.0:
                        self._kill(u)

    def _refresh_moves(self, side: Side) -> None:
        for u in self.units_of(side):
            u.moves_left = u.stats.move

    def step(self, orders: Dict[int, Order]) -> None:
        """Execute one side's turn. `orders` maps unit id -> order."""
        side = self.current_side
        # Sorted for determinism: dict order must never influence the result.
        for uid in sorted(orders.keys()):
            unit = self.units.get(uid)
            if unit is None or not unit.alive or unit.side != side:
                continue
            self._execute(unit, orders[uid])

        self._score_objectives(side)
        self._run_logistics(side)
        self.current_side = side.other
        if self.current_side is Side.BLUE:
            self.turn += 1
        # Retrace for the side about to act, so agents see current state.
        self._update_supply(self.current_side)
        self._refresh_moves(self.current_side)

    def _execute(self, unit: Unit, order: Order) -> None:
        if isinstance(order, Hold):
            if unit.stats.entrenches:
                unit.entrench = min(MAX_ENTRENCH, unit.entrench + 1)
            unit.moves_left = 0.0
        elif isinstance(order, MoveTo):
            self._move_toward(unit, order.dest)
        elif isinstance(order, Attack):
            target = self.units.get(order.target)
            if target is None or not target.alive or target.side == unit.side:
                return
            if not unit.can_engage(target):
                return          # wrong domain: the order is simply illegal
            if distance(unit.coord, target.coord) > 1:
                self._move_adjacent_to(unit, target.coord)
            if distance(unit.coord, target.coord) == 1:
                self.resolve_attack(unit, target)
        elif isinstance(order, Bombard):
            self._bombard(unit, order.dest)

    def _move_toward(self, unit: Unit, dest: Coord) -> None:
        if unit.coord == dest:
            if unit.stats.entrenches:
                unit.entrench = min(MAX_ENTRENCH, unit.entrench + 1)
            return
        domain = unit.domain
        path = self.map.find_path(
            unit.coord, dest, self.occupied(domain, exclude=unit.uid), domain
        )
        if not path:
            return
        self._walk(unit, path)

    def _move_adjacent_to(self, unit: Unit, target: Coord) -> None:
        """Close to contact with `target`.

        Path all the way to the target hex, then drop the last step: the
        second-to-last hex on the cheapest route is by construction the
        cheapest adjacent approach. One search instead of six.
        """
        domain = unit.domain
        blocked = self.occupied(domain, exclude=unit.uid)
        path = self.map.find_path(unit.coord, target, blocked, domain)
        if path:
            self._walk(unit, path[:-1])

    def _walk(self, unit: Unit, path: Iterable[Coord]) -> None:
        """Spend movement points along a path.

        Zone of control: entering a hex adjacent to a living enemy *of the
        same domain* ends movement. Cheap rule, huge tactical payoff -- it
        makes screens, flank security and delaying actions meaningful -- and
        restricting it by domain is what stops infantry from halting jets.
        """
        domain = unit.domain
        moved = False
        for coord in path:
            cost = self.map.move_cost(coord, domain)
            if cost > unit.moves_left:
                break
            stack = self._by_coord.get(coord)
            if stack and domain in stack:
                break
            unit.moves_left -= cost
            self._set_pos(unit, coord)
            moved = True
            if self.enemies_adjacent(coord, unit.side, domain):
                break
        if moved:
            unit.entrench = 0

    # -- position bookkeeping (single source of truth for the index) ------

    def _set_pos(self, unit: Unit, coord: Coord) -> None:
        old = self._by_coord.get(unit.coord)
        if old is not None and old.get(unit.domain) is unit:
            del old[unit.domain]
            if not old:
                del self._by_coord[unit.coord]
        unit.q, unit.r = coord
        self._by_coord.setdefault(coord, {})[unit.domain] = unit

    def place(self, unit: Unit, coord: Coord) -> None:
        """Public: move a unit and keep the index in sync. Assigning to
        `unit.q` / `unit.r` directly will corrupt lookups -- use this."""
        self._set_pos(unit, coord)

    def _kill(self, unit: Unit) -> None:
        unit.strength = 0.0
        stack = self._by_coord.get(unit.coord)
        if stack is not None and stack.get(unit.domain) is unit:
            del stack[unit.domain]
            if not stack:
                del self._by_coord[unit.coord]
        self.units.pop(unit.uid, None)

    # -- combat -----------------------------------------------------------

    def _attack_power(self, attacker: Unit, defender: Unit) -> float:
        st = attacker.stats
        terrain = self.map.get(defender.coord)
        mod = ATTACK_TERRAIN_MOD[attacker.utype].get(terrain, 1.0)
        power = (attacker.strength / 100.0) * st.attack * mod
        power *= supply_factor(attacker.supply)
        # Concentration bonus: other friendlies already in contact.
        supporting = 0
        for n in neighbors(*defender.coord):
            for u in self._by_coord.get(n, {}).values():
                if u.side == attacker.side and u.uid != attacker.uid and \
                        u.can_engage(defender):
                    supporting += 1
        return power * (1.0 + FLANK_BONUS * supporting)

    def _defense_power(self, defender: Unit, attacker: Optional[Unit] = None) -> float:
        st = defender.stats
        terr_mult = self.map.def_mult(defender.coord, st.domain)
        ent = 1.0 + ENTRENCH_BONUS * defender.entrench
        power = (defender.strength / 100.0) * st.defense * terr_mult * ent
        # Defence suffers less than attack: dug-in troops short of ammunition
        # still hold ground, they just cannot counterattack.
        power *= 0.5 * (1.0 + supply_factor(defender.supply))
        if attacker is not None and not defender.can_engage(attacker):
            # It cannot shoot back at this attacker's domain. Not helpless,
            # but badly placed -- otherwise air power costs nothing at all.
            power *= NO_RETURN_FIRE_MULT
        return max(power, 1e-6)

    def resolve_attack(self, attacker: Unit, defender: Unit) -> CombatResult:
        ap = self._attack_power(attacker, defender)
        dp = self._defense_power(defender, attacker)
        odds = ap / dp

        jitter_d = 0.80 + 0.40 * self.rng.random()
        jitter_a = 0.80 + 0.40 * self.rng.random()
        def_loss = _clamp(BASE_LOSS * (odds ** 0.70) * jitter_d)
        att_loss = _clamp(BASE_LOSS * (odds ** -0.50) * jitter_a)

        defender.strength -= def_loss
        attacker.strength -= att_loss
        attacker.supply = max(0.0, attacker.supply - attacker.stats.combat_cost)
        attacker.moves_left = 0.0
        attacker.entrench = 0

        destroyed = defender.strength <= 0.0
        if destroyed:
            free_hex = defender.coord
            self._kill(defender)
            # Take the ground -- otherwise breakthroughs never happen. Only
            # if this attacker's domain can actually occupy the hex.
            if attacker.alive and self._can_occupy(attacker, free_hex):
                self._set_pos(attacker, free_hex)
        if attacker.strength <= 0.0:
            self._kill(attacker)

        result = CombatResult(
            attacker.uid, defender.uid, odds, att_loss, def_loss, destroyed
        )
        self.combat_log.append(result)
        return result

    def _can_occupy(self, unit: Unit, coord: Coord) -> bool:
        domain = unit.domain
        if not self.map.passable(coord, domain):
            return False
        stack = self._by_coord.get(coord)
        return not (stack and domain in stack)

    def _bombard(self, unit: Unit, dest: Coord) -> None:
        st = unit.stats
        if not st.indirect or distance(unit.coord, dest) > st.reach:
            return
        target = None
        for u in self._by_coord.get(dest, {}).values():
            if u.side != unit.side and unit.can_engage(u):
                target = u
                break
        if target is None:
            return
        ap = (unit.strength / 100.0) * st.attack * st.fire_coef
        dp = self._defense_power(target)
        odds = ap / dp
        jitter = 0.80 + 0.40 * self.rng.random()
        loss = _clamp(BASE_LOSS * (odds ** 0.70) * jitter)
        target.strength -= loss
        unit.supply = max(0.0, unit.supply - st.combat_cost)
        unit.moves_left = 0.0
        destroyed = target.strength <= 0.0
        if destroyed:
            self._kill(target)
        self.combat_log.append(
            CombatResult(unit.uid, target.uid, odds, 0.0, loss, destroyed)
        )

    # -- scoring ----------------------------------------------------------

    def _score_objectives(self, side: Side) -> None:
        for coord, value in self.objectives.items():
            holder = self.objective_holder(coord)
            if holder == side:
                self.vp[side] += value

    def objective_holder(self, coord: Coord) -> Optional[Side]:
        """Only units that can capture hold ground. A jet overhead does not
        take a town."""
        for u in self._by_coord.get(coord, {}).values():
            if u.stats.can_capture:
                return u.side
        return None

    # -- observation ------------------------------------------------------

    def observation(self, side: Side) -> dict:
        """Full observability for now. Fog of war goes here later -- filter
        by the union of friendly `spot` radii and it just works."""
        return {
            "turn": self.turn,
            "side": int(side),
            "vp": dict(self.vp),
            "friendly": [_unit_dict(u) for u in self.units_of(side)],
            "enemy": [_unit_dict(u) for u in self.units_of(side.other)],
            "objectives": [
                {"coord": c, "value": v, "holder": self.objective_holder(c)}
                for c, v in self.objectives.items()
            ],
        }


def _clamp(x: float) -> float:
    return max(MIN_LOSS, min(MAX_LOSS, x))


def _unit_dict(u: Unit) -> dict:
    return {
        "uid": u.uid,
        "type": int(u.utype),
        "domain": int(u.domain),
        "coord": u.coord,
        "strength": u.strength,
        "entrench": u.entrench,
        "supply": u.supply,
        "in_supply": u.in_supply,
        "sidc": u.sidc(),
    }
