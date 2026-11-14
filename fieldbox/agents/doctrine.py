"""Doctrine-driven rule-based agent.

Encodes a handful of real tactical principles rather than ad-hoc heuristics:

* **Concentration / main effort (Schwerpunkt).** Pick one decisive objective
  and weight it, instead of penny-packeting across the whole frontage.
* **Force ratios.** Don't assault without local superiority. Roughly the
  conventional planning figures: ~3:1 against a prepared position in close
  terrain, ~2:1 in mixed, ~1.3:1 in a meeting engagement.
* **Combined arms.** Recon screens from a standoff observation post, fires
  units shoot from a position chosen relative to the enemy, air defence
  covers the force, air strikes what the ground force cannot reach, and
  infantry takes and holds ground.
* **Preservation.** Break contact and consolidate when combat-ineffective.

Roles are selected from unit *capabilities*, never from unit type. With nine
unit types a type-switch would already be unmanageable, and a tenth unit
should get sensible behaviour for free.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from .. import params as P
from ..sim.observation import Observation
from ..sim.orders import Attack, Bombard, Hold, MoveTo, Order
from ..core.hexgrid import Coord, Domain, Terrain, disc_offsets, distance
from dataclasses import dataclass, field

from ..sim.units import Side
from ..sim.observation import UnitView as Unit
from . import Agent, register
from .economy import FixedBuilder, engineer_order, structure_order

# Local force ratio required to launch a deliberate attack, by defender terrain.
RATIO_REQUIRED = {Terrain[t]: r for t, r in P.RATIO_REQUIRED.items()}

COMBAT_INEFFECTIVE = P.COMBAT_INEFFECTIVE
ARTY_STANDOFF = P.ARTY_STANDOFF
MIN_STANDOFF = P.MIN_STANDOFF
SAFETY_WEIGHT = P.SAFETY_WEIGHT
RECON_SEARCH_SPAN = P.RECON_SEARCH_SPAN
RECON_MIN_STANDOFF = P.RECON_MIN_STANDOFF
AIR_SEARCH_SPAN = P.AIR_SEARCH_SPAN
RESUPPLY_THRESHOLD = P.RESUPPLY_THRESHOLD
EXPOSURE_PENALTY = P.EXPOSURE_PENALTY
SAM_MIN_STANDOFF = P.SAM_MIN_STANDOFF
AIR_COVER_BONUS = P.AIR_COVER_BONUS
THREAT_RADIUS = P.THREAT_RADIUS
EXPANSION_SAFE_RADIUS = P.EXPANSION_SAFE_RADIUS


@dataclass
class Plan:
    """Everything the per-unit handlers need to know about the situation.

    Computed once per turn rather than per unit, and passed around as one
    object so adding a new consideration does not mean editing eight
    signatures.
    """
    main_effort: Optional[Coord]
    enemy_air_cover: Set[Coord]     # hexes under hostile surface-to-air fire
    own_air_cover: Set[Coord]       # hexes our own air defence protects
    claimed: Set[Coord] = field(default_factory=set)  # capitals already given to an infantry unit this turn


@register
class DoctrineAgent(Agent):
    name = "doctrine"

    def __init__(self, aggression: float = 1.0) -> None:
        self._approach_cache = {}
        self.builder = FixedBuilder()
        # <1 makes the agent more cautious, >1 more willing to attack at
        # unfavourable odds. Handy knob for building an opponent pool later.
        self.aggression = aggression

    def reset(self, side, world) -> None:
        super().reset(side, world)
        self.builder = FixedBuilder()
        self._approach_cache = {}

    # -- main ------------------------------------------------------------

    def act(self, obs: Observation) -> List[Order]:
        side = obs.side
        mine = obs.units_of(side)
        enemies = obs.units_of(side.other)
        if not mine:
            return self.builder.builds(obs)

        plan = Plan(
            main_effort=self._select_main_effort(obs, side),
            enemy_air_cover=self._air_defence_coverage(obs, side.other),
            own_air_cover=self._air_defence_coverage(obs, side),
        )
        orders: Dict[int, Order] = {}
        for unit in mine:
            # Logistics outrank tactics. A unit that is cut off and nearly dry
            # fights at a fraction of its rated power and will starve, so
            # breaking contact to reach the supply net beats any local
            # opportunity. This is checked before role dispatch so every unit
            # type gets it without nine separate copies.
            if self._needs_resupply(unit):
                step = self._resupply_move(obs, side, unit)
                if step is not None and step != unit.coord:
                    orders[unit.uid] = MoveTo(unit.uid, step)
                    continue
            if unit.stats.builds:
                eng = engineer_order(obs, unit)
                if eng is not None:
                    orders[unit.uid] = eng
                    continue
            if not enemies:                 # nothing left to fight: take the structures
                so = structure_order(obs, unit)
                if so is not None:
                    orders[unit.uid] = so
                    continue
            expand = self._expand(obs, side, unit, enemies, plan)
            if expand is not None:
                orders[unit.uid] = expand
                continue
            handler = getattr(self, f"_{self._role(unit)}")
            orders[unit.uid] = handler(obs, side, unit, enemies, plan)
        return [orders[uid] for uid in sorted(orders)] + self.builder.builds(obs)

    # -- expansion ----------------------------------------------------------

    def _expand(self, obs, side, unit, enemies, plan) -> Optional[Order]:
        """Fast capturing units take unheld capitals while the way is clear.

        Provinces score every turn for their owner, so early expansion
        decides the VP race. Returns None to fall back to the normal role.
        """
        st = unit.stats
        if not (st.can_capture and st.move >= 7 and st.domain is Domain.LAND):
            return None
        land = [e for e in enemies if e.domain is Domain.LAND]
        if any(distance(e.coord, unit.coord) <= EXPANSION_SAFE_RADIUS for e in land):
            return None
        if unit.coord in obs.objectives:
            if obs.objective_holder(unit.coord) != side:
                plan.claimed.add(unit.coord)
                return Hold(unit.uid)
            if any(e.stats.can_capture and distance(e.coord, unit.coord) <= THREAT_RADIUS
                   for e in land):
                return None
        unheld = [
            c for c in obs.objectives
            if obs.objective_holder(c) != side and c not in plan.claimed
            and c != unit.coord and obs.map.passable(c, unit.domain)
        ]
        for c in sorted(unheld, key=lambda c: (distance(unit.coord, c), c)):
            if obs.path_cost(unit.coord, c, unit.domain) is None:
                continue
            occ = obs.unit_at(c, unit.domain)
            if occ is not None and occ.side != side:
                continue
            plan.claimed.add(c)
            return MoveTo(unit.uid, c)
        # Nothing left to take: raid an undefended enemy structure.
        return structure_order(obs, unit, undefended=True)

    # -- logistics --------------------------------------------------------

    def _needs_resupply(self, unit: Unit) -> bool:
        return not unit.in_supply and unit.supply < RESUPPLY_THRESHOLD

    def _resupply_move(self, obs, side, unit) -> Optional[Coord]:
        """Nearest hex this unit's domain can draw supply from.

        For aircraft this is what produces the sortie cycle: they burn stock
        roughly ten times faster than infantry, run dry over the objective,
        and fly home to rearm rather than loitering until they are shot down.
        """
        net = obs.supply_net.get(side, {}).get(unit.domain)
        if not net:
            return None
        best = None
        best_d = 10 ** 9
        for coord in net:
            occupant = obs.unit_at(coord, unit.domain)
            if occupant is not None and occupant.uid != unit.uid:
                continue
            d = distance(unit.coord, coord)
            if d < best_d or (d == best_d and (best is None or coord < best)):
                best_d = d
                best = coord
        return best

    # -- role selection ---------------------------------------------------

    def _role(self, unit: Unit) -> str:
        """Dispatch on capabilities, never on unit type.

        A new unit type gets sensible behaviour automatically as long as its
        stats describe what it is: something that only shoots at aircraft is
        air defence, something that shoots at range is a fires unit, and so
        on.
        """
        st = unit.stats
        if st.domain is Domain.AIR:
            return "air"
        if st.targets == frozenset({Domain.AIR}):
            return "air_defence"
        if not st.can_capture and not st.indirect and st.attack < 0.5:
            return "support"        # engineers: keep them back until M4 gives them work
        if st.indirect:
            return "fires"          # artillery, and naval gunfire
        if not st.entrenches and st.attack < 0.7:
            return "screen"         # recon
        if st.move >= 7:
            return "manoeuvre"      # tanks, IFVs
        return "infantry"

    # -- planning ---------------------------------------------------------

    def _select_main_effort(self, obs: Observation, side: Side) -> Optional[Coord]:
        """Highest value objective, tie-broken toward the one we can reach
        soonest. Held-and-uncontested objectives are deprioritised."""
        capturing = [u for u in obs.units_of(side) if u.stats.can_capture]
        if not capturing or not obs.objectives:
            return None
        best = None
        best_score = -1e9
        for coord, value in obs.objectives.items():
            holder = obs.objective_holder(coord)
            if holder == side and not obs.enemies_adjacent(coord, side):
                continue  # already secure, don't waste the main effort
            reach = min(distance(u.coord, coord) for u in capturing)
            # Provinces keep their owner, so an enemy-held capital is a
            # double swing (their VP stop, ours start); a neutral one is single.
            swing = 2.0 if holder is not None and holder != side else 1.0
            score = value * 10.0 * swing - reach
            if score > best_score:
                best_score = score
                best = coord
        if best is None:
            best = max(obs.objectives.items(), key=lambda kv: kv[1])[0]
        return best

    def _air_defence_coverage(self, obs: Observation, side: Side) -> Set[Coord]:
        """Hexes under enemy surface-to-air cover.

        Flying through this is how air assets die, so every air movement
        decision is filtered through it.
        """
        covered: Set[Coord] = set()
        for u in obs.units_of(side):
            st = u.stats
            if Domain.AIR not in st.targets:
                continue
            uq, ur = u.coord
            for dq, dr in disc_offsets(st.reach):
                covered.add((uq + dq, ur + dr))
        return covered

    def _local_odds(self, obs: Observation, attacker: Unit, defender: Unit) -> float:
        ap = obs.attack_power(attacker, defender)
        dp = obs.defense_power(defender, attacker)
        return ap / dp if dp > 0 else 99.0

    def _exposure(self, obs, side, unit, defender, enemies) -> int:
        """How many enemies could engage `unit` where the assault leaves it.

        Attacking spends all movement and resets entrenchment, so the
        attacker ends the turn stationary and in the open next to the
        objective. Tanks were dying at a median of 4:1 odds *against* them at
        full supply -- they were not losing the fights they started, they
        were being swarmed on the following turn.
        """
        threats = sum(
            1 for e in enemies
            if e.uid != defender.uid and e.can_engage(unit)
            and distance(defender.coord, e.coord) <= e.stats.reach + 1
        )
        # Flank security comes from friends. Counting only enemies made a
        # lone tank face a median of four threats with one supporting unit
        # and demand 2.5:1 before moving -- so it declined 61% of assaults,
        # sat still, and absorbed the attacks anyway. Exposure is what is
        # left after mutual support.
        support = sum(
            1 for f in obs.units_of(side)
            if f.uid != unit.uid and f.stats.can_capture
            and distance(defender.coord, f.coord) <= 2
        )
        return max(0, threats - support)

    def _should_assault(self, obs: Observation, attacker: Unit, defender: Unit,
                        enemies=None) -> bool:
        terrain = obs.map.get(defender.coord)
        required = RATIO_REQUIRED.get(terrain, 2.0) / max(self.aggression, 0.05)
        required *= 1.0 + 0.15 * defender.entrench
        if enemies:
            # Flank security: every additional enemy that can reach the
            # objective raises the bar for going in.
            exposure = self._exposure(obs, attacker.side, attacker, defender, enemies)
            required *= 1.0 + EXPOSURE_PENALTY * max(0, exposure - 1)
        return self._local_odds(obs, attacker, defender) >= required

    def _engageable(self, obs: Observation, unit: Unit, enemies: List[Unit]) -> List[Unit]:
        return [e for e in enemies if unit.can_engage(e)]

    def _best_target(
        self, obs: Observation, unit: Unit, enemies: List[Unit], radius: int = 4
    ) -> Optional[Unit]:
        """Prefer close, weak, and objective-holding enemies."""
        candidates = [
            e for e in self._engageable(obs, unit, enemies)
            if distance(unit.coord, e.coord) <= radius
        ]
        if not candidates:
            return None

        def value(e: Unit) -> float:
            score = -distance(unit.coord, e.coord) * 2.0
            score += (100.0 - e.strength) / 25.0          # finish the wounded
            if e.coord in obs.objectives:
                score += 4.0 * obs.objectives[e.coord]   # ground that scores
            score -= 2.0 * obs.map.def_mult(e.coord, e.domain)
            return score

        return max(candidates, key=lambda e: (value(e), -e.uid))

    # -- shared positioning ------------------------------------------------

    def _standoff_position(
        self, obs, side, unit, enemies, effect_range, min_standoff,
        max_span=None, own_air_cover=None,
    ) -> Optional[Coord]:
        """Best hex that keeps `unit` useful but out of contact.

        Shared by the guns, the scouts and the aircraft, because they had the
        same failure mode: fragile units whose job is done at a distance,
        walking onto the objective and dying. "Useful" means covering enemies
        within `effect_range` -- weapon reach for fires, observation radius
        for recon.

        Candidates come from a cached hex disc, so this is ~37 hexes at
        radius 3 rather than a sweep of the whole map.
        """
        engageable = self._engageable(obs, unit, enemies)
        if not engageable:
            return None
        domain = unit.domain
        span = int(unit.stats.move)
        if max_span is not None:
            span = min(span, max_span)
        best = None
        best_score = -1e9
        uq, ur = unit.coord
        for dq, dr in disc_offsets(span):
            coord = (uq + dq, ur + dr)
            if not obs.map.passable(coord, domain):
                continue
            occupant = obs.unit_at(coord, domain)
            if occupant is not None and occupant.uid != unit.uid:
                continue
            # Standoff only makes sense against threats you can outrange.
            # A helicopter moves 10 and a jet 16, so no distance is "safe"
            # from air -- the counter to air is friendly air defence, which
            # is scored as a bonus below rather than as a hard constraint.
            ground_threats = [
                e for e in enemies
                if e.can_engage(unit) and e.domain is not Domain.AIR
            ]
            nearest = min(
                (distance(coord, e.coord) for e in ground_threats), default=99
            )
            if nearest < min_standoff:
                continue
            covered = sum(
                1 for e in engageable
                if distance(coord, e.coord) <= effect_range
            )
            if covered == 0:
                continue
            travel = (abs(dq) + abs(dq + dr) + abs(dr)) // 2
            score = 10.0 * covered + SAFETY_WEIGHT * min(nearest, 8) - 1.5 * travel
            if own_air_cover and coord in own_air_cover:
                score += AIR_COVER_BONUS
            # Explicit coordinate tie-break: without it the chosen hex
            # depends on enumeration order, so changing how candidates are
            # generated silently changes every obs.
            if best is None or score > best_score or (
                score == best_score and coord < best
            ):
                best_score = score
                best = coord
        return best

    def _advance_position(
        self, obs, side, unit, enemies, goal, min_standoff, max_span=None,
        avoid: Optional[Set[Coord]] = None,
    ) -> Optional[Coord]:
        """Move toward `goal` without ending the turn inside `min_standoff`.

        A screen advances with the main body, it does not race ahead of it.
        Recon moves 9, so an unconstrained MoveTo(objective) put it on the
        contested hex in a single bound -- which is how it died in every obs.
        """
        if goal is None:
            return None
        domain = unit.domain
        span = int(unit.stats.move)
        if max_span is not None:
            span = min(span, max_span)
        threats = [e for e in enemies if e.can_engage(unit)]
        best = None
        best_score = -1e9
        uq, ur = unit.coord
        for dq, dr in disc_offsets(span):
            coord = (uq + dq, ur + dr)
            if not obs.map.passable(coord, domain):
                continue
            occupant = obs.unit_at(coord, domain)
            if occupant is not None and occupant.uid != unit.uid:
                continue
            if avoid and coord in avoid:
                continue
            nearest = min((distance(coord, e.coord) for e in threats), default=99)
            if nearest < min_standoff:
                continue
            score = -10.0 * distance(coord, goal) + nearest
            if best is None or score > best_score or (
                score == best_score and coord < best
            ):
                best_score = score
                best = coord
        return best

    def _firing_position(self, obs, side, unit, enemies, plan) -> Optional[Coord]:
        """Gun line: inside our own reach, outside theirs, and under our own
        air-defence umbrella where one exists."""
        return self._standoff_position(
            obs, side, unit, enemies,
            effect_range=unit.stats.reach, min_standoff=MIN_STANDOFF,
            own_air_cover=plan.own_air_cover,
        )

    def _screen_position(self, obs, side, unit, enemies) -> Optional[Coord]:
        """Observation post: enemy under observation, at maximum distance.

        The standoff is derived from the observation radius rather than being
        a free parameter. It has to be, because `_standoff_position` requires
        an enemy within `effect_range` AND no enemy closer than
        `min_standoff` -- so a standoff larger than the observation radius
        makes every hex invalid and the search silently returns None.

        The floor matters: with an observation radius of 2 the only valid
        post is 2 hexes from the enemy, which is contact, not observation.
        """
        return self._standoff_position(
            obs, side, unit, enemies,
            effect_range=unit.stats.spot,
            min_standoff=max(RECON_MIN_STANDOFF, unit.stats.spot),
            max_span=RECON_SEARCH_SPAN,
        )

    def _unanswerable_standoff(self, unit, enemies, floor: int) -> int:
        """Distance that keeps `unit` outside the reach of anything it cannot
        shoot back at.

        Threats you can answer are a fair fight and standoff is optional.
        Threats you cannot answer must be kept outside their weapon reach --
        otherwise the unit simply stands there and dies, which is exactly what
        a SAM battery did for 32 turns while artillery shelled it.

        The old code used a fixed constant of 3, which is *equal* to tube
        artillery's reach, so "standing off" put the battery precisely inside
        the beaten zone.
        """
        reaches = [
            e.stats.reach for e in enemies
            if e.can_engage(unit) and not unit.can_engage(e)
        ]
        return max(floor, max(reaches) + 1) if reaches else floor

    def _approach_hex(self, obs, unit, goal):
        """Closest hex to `goal` that this unit's domain can actually occupy.

        A ship ordered to an inland objective finds no path and falls through
        to Hold, which is why frigates sat in the corner for entire games.
        The map is static, so this is cached.
        """
        if goal is None:
            return None
        domain = unit.domain
        key = (domain, goal)
        cached = self._approach_cache.get(key)
        if cached is not None:
            return cached
        best = None
        best_d = 10 ** 9
        for coord in obs.map.all_coords():
            if not obs.map.passable(coord, domain):
                continue
            d = distance(coord, goal)
            if d < best_d or (d == best_d and (best is None or coord < best)):
                best_d = d
                best = coord
        self._approach_cache[key] = best
        return best

    def _enemy_threat_range(self, enemies) -> int:
        """Furthest any surviving enemy could move in one turn."""
        return max((int(e.stats.move) for e in enemies), default=0)

    def _withdraw(self, obs, side, unit, plan) -> Order:
        """Break contact toward the nearest friendly-held or rear objective."""
        home_col = 0 if side is Side.BLUE else obs.map.width - 1
        rear = None
        best = 1e9
        for coord in obs.objectives:
            if not obs.map.passable(coord, unit.domain):
                continue
            col = coord[0] + coord[1] // 2
            d = abs(col - home_col)
            if d < best:
                best = d
                rear = coord
        return MoveTo(unit.uid, rear) if rear else Hold(unit.uid)

    # -- role behaviours ---------------------------------------------------

    def _support(self, obs, side, unit, enemies, plan) -> Order:
        """Stay near the HQ; a weak unit that cannot take ground has no
        business at the front."""
        hq = obs.hq[side]
        if distance(unit.coord, hq) <= 2:
            return Hold(unit.uid)
        return MoveTo(unit.uid, hq)

    def _manoeuvre(self, obs, side, unit, enemies, plan,
                   exploit: bool = True) -> Order:
        if unit.strength < COMBAT_INEFFECTIVE and obs.enemies_adjacent(unit.coord, side):
            return self._withdraw(obs, side, unit, plan)

        adjacent = [
            e for e in obs.enemies_adjacent(unit.coord, side) if unit.can_engage(e)
        ]
        if adjacent:
            target = min(adjacent, key=lambda e: (e.strength, e.uid))
            if self._should_assault(obs, unit, target, enemies):
                return Attack(unit.uid, target.uid)
            return Hold(unit.uid)

        radius = 6 if exploit else 4
        target = self._best_target(obs, unit, enemies, radius=radius)
        if target is not None and self._should_assault(obs, unit, target, enemies):
            return Attack(unit.uid, target.uid)
        if plan.main_effort is not None:
            return MoveTo(unit.uid, plan.main_effort)
        return Hold(unit.uid)

    def _infantry(self, obs, side, unit, enemies, plan) -> Order:
        if unit.strength < COMBAT_INEFFECTIVE and obs.enemies_adjacent(unit.coord, side):
            return self._withdraw(obs, side, unit, plan)

        # A capital stays ours once taken (provinces keep their owner), so
        # infantry holds one only until it flips, or while the enemy is close
        # enough to retake it -- then moves on to the next one. (Under the
        # old rules an objective scored only while occupied, so it sat.)
        if unit.coord in obs.objectives:
            adjacent = [
                e for e in obs.enemies_adjacent(unit.coord, side)
                if unit.can_engage(e)
            ]
            if adjacent:
                target = min(adjacent, key=lambda e: (e.strength, e.uid))
                if self._should_assault(obs, unit, target, enemies):
                    return Attack(unit.uid, target.uid)
            threatened = any(e.stats.can_capture and distance(e.coord, unit.coord) <= THREAT_RADIUS
                             for e in enemies)
            if obs.objective_holder(unit.coord) != side or threatened:
                return Hold(unit.uid)

        unheld = [
            c for c in obs.objectives
            if obs.objective_holder(c) != side
            and obs.map.passable(c, unit.domain)
        ]
        free = [c for c in unheld if c not in plan.claimed] or unheld
        if unheld:
            # Several units may converge on the main effort (mass for the
            # assault); other capitals are taken one unit each.
            goal = plan.main_effort if plan.main_effort in unheld else min(
                free, key=lambda c: (distance(unit.coord, c), c)
            )
            if goal != plan.main_effort:
                plan.claimed.add(goal)
            occupant = obs.unit_at(goal, unit.domain)
            if occupant is not None and occupant.side != side \
                    and unit.can_engage(occupant):
                if self._should_assault(obs, unit, occupant, enemies):
                    return Attack(unit.uid, occupant.uid)
            return MoveTo(unit.uid, goal)
        return self._manoeuvre(
            obs, side, unit, enemies, plan, exploit=False
        )

    def _screen(self, obs, side, unit, enemies, plan) -> Order:
        """Screen and find. Recon fights only to finish something crippled.

        It used to be ordered straight at the main effort, which is where the
        enemy is -- with a 0.60 defence coefficient it died in every obs.
        """
        adjacent = [
            e for e in obs.enemies_adjacent(unit.coord, side) if unit.can_engage(e)
        ]
        if adjacent:
            weakest = min(adjacent, key=lambda e: (e.strength, e.uid))
            if weakest.strength < 20.0:
                return Attack(unit.uid, weakest.uid)
            post = self._screen_position(obs, side, unit, enemies)
            if post is not None and post != unit.coord:
                return MoveTo(unit.uid, post)
            return self._withdraw(obs, side, unit, plan)

        post = self._screen_position(obs, side, unit, enemies)
        if post is not None:
            return MoveTo(unit.uid, post) if post != unit.coord else Hold(unit.uid)

        standoff = max(RECON_MIN_STANDOFF, unit.stats.spot)
        step = self._advance_position(
            obs, side, unit, enemies, plan.main_effort, standoff,
            max_span=RECON_SEARCH_SPAN,
        )
        if step is not None and step != unit.coord:
            return MoveTo(unit.uid, step)
        threats = [e for e in enemies if e.can_engage(unit)]
        if threats:
            nearest = min(distance(unit.coord, e.coord) for e in threats)
            if nearest < standoff:
                return self._withdraw(obs, side, unit, plan)
        return Hold(unit.uid)

    def _fires(self, obs, side, unit, enemies, plan) -> Order:
        """Artillery and naval gunfire: shoot from a position chosen relative
        to the enemy, not relative to the objective. Positioning on the
        objective is what killed the guns in 30 games out of 30."""
        st = unit.stats
        engageable = self._engageable(obs, unit, enemies)
        in_range = [
            e for e in engageable if distance(unit.coord, e.coord) <= st.reach
        ]
        if in_range:
            def priority(e: Unit) -> float:
                score = (100.0 - e.strength) / 20.0
                if e.coord in obs.objectives:
                    score += 5.0 * obs.objectives[e.coord]
                if e.coord == plan.main_effort:
                    score += 6.0
                return score

            threatening = [
                e for e in obs.enemies_adjacent(unit.coord, side)
                if e.can_engage(unit)
            ]
            if threatening:
                spot = self._firing_position(obs, side, unit, enemies, plan)
                if spot is not None:
                    return MoveTo(unit.uid, spot)
                return self._withdraw(obs, side, unit, plan)
            best = max(in_range, key=lambda e: (priority(e), -e.uid))
            return Bombard(unit.uid, best.coord)

        spot = self._firing_position(obs, side, unit, enemies, plan)
        if spot is not None and spot != unit.coord:
            return MoveTo(unit.uid, spot)
        # Nothing in range and no firing position: close on the objective as
        # far as this domain allows. A ship cannot enter an inland hex, so
        # MoveTo(objective) silently failed and the frigate idled all obs.
        approach = self._approach_hex(obs, unit, plan.main_effort)
        if approach is not None and approach != unit.coord:
            firing_distance = max(2, st.reach - ARTY_STANDOFF)
            if distance(unit.coord, approach) > firing_distance or \
                    unit.domain is not Domain.LAND:
                return MoveTo(unit.uid, approach)
        return Hold(unit.uid)

    def _air_defence(self, obs, side, unit, enemies, plan) -> Order:
        """Surface-to-air: the umbrella over the force.

        It only engages aircraft, so it has nothing to do about ground
        attack except stay alive and stay useful -- which means sitting with
        the force it is protecting rather than pushing forward alone.
        """
        st = unit.stats
        hostile_air = [e for e in enemies if unit.can_engage(e)]
        in_range = [
            e for e in hostile_air if distance(unit.coord, e.coord) <= st.reach
        ]
        if in_range:
            best = min(in_range, key=lambda e: (e.strength, e.uid))
            return Bombard(unit.uid, best.coord)

        # Cover the force. Moving onto the objective is the same mistake the
        # guns used to make: the objective is where the enemy is, and air
        # defence cannot shoot back at ground troops at all.
        post = self._umbrella_position(obs, side, unit, enemies)
        if post is not None and post != unit.coord:
            return MoveTo(unit.uid, post)

        # Standing still under fire it cannot answer is never right.
        standoff = self._unanswerable_standoff(unit, enemies, SAM_MIN_STANDOFF)
        under_fire = [
            e for e in enemies
            if e.can_engage(unit) and not unit.can_engage(e)
            and distance(unit.coord, e.coord) <= e.stats.reach
        ]
        if under_fire:
            step = self._advance_position(
                obs, side, unit, enemies, self._rear_hex(obs, side), standoff,
            )
            if step is not None and step != unit.coord:
                return MoveTo(unit.uid, step)
            return self._withdraw(obs, side, unit, plan)
        return Hold(unit.uid)

    def _umbrella_position(self, obs, side, unit, enemies) -> Optional[Coord]:
        """Where air defence should sit: over its own force, behind the line.

        Scores hexes by how many friendlies fall inside `reach`, with a hard
        floor on distance to enemy ground -- a SAM battery cannot engage
        ground at all, so anything that reaches it kills it for free.
        """
        # Every friendly unit, not just the ones that take ground. The first
        # version filtered on `can_capture`, which excluded artillery and
        # other SAM batteries -- precisely the soft, high-value assets an
        # air-defence umbrella exists to protect.
        friends = [u for u in obs.units_of(side) if u.uid != unit.uid]
        if not friends:
            return None
        # Weight by how badly a unit needs cover: something that cannot shoot
        # back at aircraft and has a low defence coefficient is what air
        # goes hunting for.
        weights = {
            f.uid: 1.0 + 2.0 * (1.0 - min(f.stats.defense, 1.0))
            + (1.5 if Domain.AIR not in f.stats.targets else 0.0)
            for f in friends
        }
        ground_threats = [
            e for e in enemies if e.domain is not Domain.AIR and e.can_engage(unit)
        ]
        standoff = self._unanswerable_standoff(unit, enemies, SAM_MIN_STANDOFF)
        domain = unit.domain
        span = int(unit.stats.move)
        reach = unit.stats.reach
        best = None
        best_score = -1e9
        uq, ur = unit.coord
        for dq, dr in disc_offsets(span):
            coord = (uq + dq, ur + dr)
            if not obs.map.passable(coord, domain):
                continue
            occupant = obs.unit_at(coord, domain)
            if occupant is not None and occupant.uid != unit.uid:
                continue
            nearest = min(
                (distance(coord, e.coord) for e in ground_threats), default=99
            )
            if nearest < standoff:
                continue
            covered = sum(
                weights[f.uid] for f in friends
                if distance(coord, f.coord) <= reach
            )
            travel = (abs(dq) + abs(dq + dr) + abs(dr)) // 2
            score = 10.0 * covered + 2.0 * min(nearest, 8) - 1.0 * travel
            if best is None or score > best_score or (
                score == best_score and coord < best
            ):
                best_score = score
                best = coord
        return best

    def _air(self, obs, side, unit, enemies, plan) -> Order:
        """Strike aircraft.

        Air ignores terrain and zones of control, which makes it devastating
        against anything soft and defenceless against surface-to-air fire.
        The whole behaviour is therefore built around `air_threat`: strike
        what is not covered, and do not loiter under a SAM umbrella.
        """
        if unit.strength < COMBAT_INEFFECTIVE:
            step = self._advance_position(
                obs, side, unit, enemies,
                self._rear_hex(obs, side), 0,
                max_span=AIR_SEARCH_SPAN, avoid=plan.enemy_air_cover,
            )
            if step is not None:
                return MoveTo(unit.uid, step)
            return Hold(unit.uid)

        engageable = self._engageable(obs, unit, enemies)
        uncovered = [e for e in engageable if e.coord not in plan.enemy_air_cover]

        if engageable:
            def value(e: Unit) -> float:
                score = -1.5 * distance(unit.coord, e.coord)
                score += (100.0 - e.strength) / 20.0
                if e.coord in obs.objectives:
                    score += 4.0 * obs.objectives[e.coord]
                # Soft, high-value targets are what air is for.
                score += 3.0 * (1.0 - e.stats.defense)
                return score

        # No enemy unit worth chasing nearby: bomb infrastructure that is not
        # under SAM cover (HQ first on ties).
        if not any(distance(unit.coord, e.coord) <= AIR_SEARCH_SPAN for e in engageable):
            so = structure_order(obs, unit, avoid=plan.enemy_air_cover)
            if so is not None:
                return so

        if uncovered:
            target = max(uncovered, key=lambda e: (value(e), -e.uid))
            return Attack(unit.uid, target.uid)

        # Every target sits under a surface-to-air umbrella. The previous
        # version had `or not uncovered` on the attack branch, which meant
        # "if everything is covered, attack anyway" -- it made the standoff
        # code below unreachable and cost 15 of 16 aircraft to SAM fire.
        #
        # No viable target means go home. Loitering is worse than useless:
        # aircraft burn stock ten times faster than infantry, so orbiting a
        # denied objective just runs them dry outside their own supply. The
        # counter to air defence is ground manoeuvre overrunning it, not
        # flying into it.
        home = self._rear_hex(obs, side)
        step = self._advance_position(
            obs, side, unit, enemies, home, 0,
            max_span=AIR_SEARCH_SPAN, avoid=plan.enemy_air_cover,
        )
        if step is not None and step != unit.coord:
            return MoveTo(unit.uid, step)
        return Hold(unit.uid)

    def _rear_hex(self, obs, side) -> Optional[Coord]:
        home_col = 0 if side is Side.BLUE else obs.map.width - 1
        row = obs.map.height // 2
        return (home_col - row // 2, row)
