"""Commander: the strategic layer (PLAN.md 4.2).

Each turn it scores objectives with the genome's weights, picks a main (and
possibly a secondary) effort, reserves garrisons and a reserve, assigns every
unit a task, and plans builds. The per-unit handlers live in tactical.py and
carry the task out. In-match adaptation (PLAN.md 4.4) nudges build and
garrison weights toward counters to what the enemy is seen fielding.

Agents expose two optional hooks used by the match runner: `tasks()` (uid ->
task text) and `drain_events()` (`adapted` events). The engine never sees them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Set, Tuple

from .. import params as P
from ..core.hexgrid import Coord, Domain, distance
from ..sim.observation import Observation
from ..sim.orders import Attack, Build, Construct, Hold, MoveTo, Order, Repair, Strike
from ..sim.structures import StructureType as ST
from ..sim.units import STATS as USTATS, Side, UnitType
from . import register
from .genome import DoctrineGenome
from .tactical import TacticalAgent, Plan

C = P.COMMANDER
STRUCT_BASE = dict(C.structure_base)
# Which genome trait weights a structure kind as a target / as an asset to defend.
TARGET_TRAIT = {ST.HQ: "target_hq", ST.INDUSTRY: "target_industry", ST.POWER: "target_power",
                ST.DATACENTER: "target_datacenter", ST.AIRFIELD: "target_airfield",
                ST.PORT: "target_airfield"}
DEFEND_TRAIT = {ST.HQ: "defend_hq", ST.INDUSTRY: "defend_industry", ST.POWER: "defend_power",
                ST.DATACENTER: "defend_datacenter"}


@dataclass(frozen=True)
class Target:
    key: tuple                   # stable identity, for hysteresis
    label: str
    coord: Coord
    score: float
    sid: Optional[int] = None    # set for structure targets


def _pts(u) -> float:
    """Combat value of a unit in cost-points."""
    return u.stats.cost * u.strength / 100.0


@register
class Commander(TacticalAgent):
    name = "commander"

    def __init__(self, genome: Optional[DoctrineGenome] = None, label: Optional[str] = None) -> None:
        self.genome = genome or DoctrineGenome.default()
        super().__init__(aggression=1.0 / self.genome.ratio_mult)
        self.withdraw_below = self.genome.withdraw_below
        if label:
            self.name = label
        self._init_state()

    def _init_state(self) -> None:
        self._main: Optional[tuple] = None
        self._tasks: Dict[int, str] = {}
        self._last_task: Dict[int, str] = {}
        self._events: List[dict] = []
        self._seen_types: Dict[str, int] = {}      # enemy type name -> last turn seen
        self._prev_strength: Dict[int, float] = {}
        self._hit: Dict[str, int] = {}             # own structure kind -> last turn hit
        self.build_boost: Dict[UnitType, float] = {}
        self.defend_boost: Dict[str, float] = {}

    def reset(self, side, world) -> None:
        super().reset(side, world)
        self._init_state()

    # -- hooks for the match runner ----------------------------------------

    def tasks(self) -> Dict[int, str]:
        return dict(self._tasks)

    def drain_events(self) -> List[dict]:
        ev, self._events = self._events, []
        return ev

    # -- adaptation (PLAN 4.4) ---------------------------------------------

    def _observe(self, obs: Observation) -> None:
        for e in list(obs.enemy_units) + list(obs.ghosts):
            self._seen_types[e.utype.name] = obs.turn
        for s in obs.own_structures:
            prev = self._prev_strength.get(s.sid)
            if prev is not None and prev - s.strength >= C.own_hit_drop:
                self._hit[s.kind.name] = obs.turn
            self._prev_strength[s.sid] = s.strength

    def _adapt(self, obs: Observation) -> None:
        a = self.genome["adaptivity"]
        now = list(obs.enemy_units) + list(obs.ghosts)
        counts: Dict[str, float] = {}
        for e in now:
            counts[e.utype.name] = counts.get(e.utype.name, 0.0) + 1.0
        for name, t in sorted(self._seen_types.items()):
            if name not in counts and obs.turn - t <= C.adapt_window:
                counts[name] = 0.5
        total = sum(counts.values())
        boost: Dict[UnitType, float] = {}
        if total > 0 and a > 0:
            for name in sorted(counts):
                counters = P.COUNTER_TABLE.get(name, ())
                for c in counters:
                    ut = UnitType[c]
                    boost[ut] = boost.get(ut, 0.0) + a * counts[name] / total / len(counters)
        old = self.build_boost
        if any(abs(boost.get(t, 0.0) - old.get(t, 0.0)) >= C.adapt_min_change for t in UnitType):
            self.build_boost = boost
            self._events.append({"k": "adapted", "s": int(obs.side), "d": {
                "mode": "build",
                "enemy": {n: counts[n] for n in sorted(counts)},
                "shifts": {t.name: round(v, 2) for t, v in sorted(boost.items())}}})
        dboost = {k: min(1.0, a * C.adapt_defend_gain)
                  for k, t in sorted(self._hit.items()) if obs.turn - t <= C.adapt_window}
        if set(dboost) != set(self.defend_boost):
            self.defend_boost = dboost
            self._events.append({"k": "adapted", "s": int(obs.side), "d": {
                "mode": "defend", "kinds": sorted(dboost)}})

    def _defend_weight(self, kind: ST) -> float:
        tr = DEFEND_TRAIT.get(kind)
        if tr is None:
            return 0.0
        return min(1.0, self.genome[tr] + self.defend_boost.get(kind.name, 0.0))

    # -- target scoring -----------------------------------------------------

    def _enemy_pts_near(self, obs: Observation, coord: Coord) -> float:
        return sum(_pts(e) for e in list(obs.enemy_units) + list(obs.ghosts)
                   if distance(e.coord, coord) <= 2)

    def _targets(self, obs: Observation, force: list) -> List[Target]:
        g = self.genome
        side = obs.side
        w = obs.map.width
        if force:
            cq = sum(u.q for u in force) / len(force)
            cr = sum(u.r for u in force) / len(force)
            centre = (int(round(cq)), int(round(cr)))
        else:
            centre = obs.hq[side]
        div = max(1.0, w * C.dist_div_frac)
        strike = sum(_pts(u) for u in force)
        need = C.base_ratio * g.ratio_mult
        expansion = g["expansion"]
        threats = list(obs.enemy_units) + list(obs.ghosts)

        def feasible(coord: Coord, enemy_held: bool) -> float:
            d = self._enemy_pts_near(obs, coord)
            if enemy_held and coord not in obs.visible_land:
                d += C.assumed_defence
            ratio = strike / max(d, 1.0)
            return min(1.0, ratio / need) ** 2

        out: List[Target] = []
        for coord, value in obs.objectives.items():
            holder = obs.objective_holder(coord)
            if holder == side:
                if not any(distance(e.coord, coord) <= 3 for e in threats):
                    continue                       # secure and quiet
                base, wt, fz = value * C.horizon, 1.0, 1.0
            elif holder is None:
                base, wt = value * C.horizon, 2.0 * expansion
                fz = feasible(coord, False)
            else:
                base, wt = value * C.horizon * 2.0, 2.0 * (1.0 - expansion)
                fz = feasible(coord, True)
            sc = base * wt * fz / (1.0 + distance(centre, coord) / div)
            out.append(Target(("c", coord), f"cap {coord[0]},{coord[1]}", coord, sc))
        for s in obs.enemy_structures:
            base = STRUCT_BASE.get(s.kind.name, 10.0)
            tr = TARGET_TRAIT.get(s.kind)
            tv = g[tr] if tr else (g["target_industry"] + g["target_power"]) / 2.0
            wt = 2.0 * (1.0 - expansion) * 2.0 * tv
            fz = feasible(s.coord, True)
            sc = base * wt * fz / (1.0 + distance(centre, s.coord) / div)
            out.append(Target(("s", s.sid), f"{s.kind.name} S{s.sid}", s.coord, sc, s.sid))
        land_e = [e for e in obs.enemy_units if e.domain is Domain.LAND]
        land_g = [e for e in obs.ghosts if e.domain is Domain.LAND]
        groups: List[Target] = []
        for e, age in [(e, 0) for e in land_e] + [(e, e.age) for e in land_g]:
            grp = sum(_pts(o) for o in threats if distance(o.coord, e.coord) <= 2)
            base = C.unit_group_coef * grp / (1.0 + age)
            wt = 2.0 * g["target_units"]
            fz = min(1.0, strike / max(grp, 1.0) / need) ** 2
            sc = base * wt * fz / (1.0 + distance(centre, e.coord) / div)
            kind = "u" if age == 0 else "g"
            groups.append(Target((kind, e.uid), f"units {e.coord[0]},{e.coord[1]}", e.coord, sc))
        groups.sort(key=lambda t: (-t.score, t.key))
        out += groups[:6]
        out.sort(key=lambda t: (-t.score, t.key))
        return out

    def _pick(self, cands: List[Target]) -> Tuple[Optional[Target], Optional[Target]]:
        if not cands:
            return None, None
        best = cands[0]
        main = best
        if self._main is not None:
            cur = next((t for t in cands if t.key == self._main), None)
            if cur is not None and cur.score * C.hysteresis >= best.score:
                main = cur
        self._main = main.key
        second = next((t for t in cands
                       if t.key != main.key and t.score > 0
                       and distance(t.coord, main.coord) >= C.secondary_min_dist), None)
        return main, second

    # -- building -----------------------------------------------------------

    def _pp_reserve(self, obs: Observation) -> float:
        n = len(obs.structures_of(obs.side, ST.FOB)) + len(obs.building)
        if n >= P.TEMP_MAX_FOBS or not any(u.stats.builds for u in obs.own_units):
            return 0.0
        return self.genome["econ_invest"] * C.econ_floor_mult * P.ECONOMY.fob_cost

    def _builds(self, obs: Observation) -> List[Build]:
        out: List[Build] = []
        pp = obs.pp - self._pp_reserve(obs)
        have: Dict[UnitType, int] = {}
        for u in obs.own_units:
            have[u.utype] = have.get(u.utype, 0) + 1
        for q in obs.queues.values():
            for ut, _ in q:
                have[ut] = have.get(ut, 0) + 1
        base = self.genome.build_weights()
        for st in sorted(obs.own_structures, key=lambda s: s.sid):
            dom = st.stats.produces
            if dom is None:
                continue
            types = [t for t in UnitType if USTATS[t].domain is dom]
            tot = sum(base[t] for t in types)
            wn = {t: (base[t] / tot if tot > 0 else 0.0) + self.build_boost.get(t, 0.0)
                  for t in types}
            if tot <= 0:                                  # no base weight: only adapted types
                wn = {t: v for t, v in wn.items() if t in self.build_boost}
            wsum = sum(wn.values())
            if wsum <= 0:
                continue
            room = P.ECONOMY.max_queue - len(obs.queues.get(st.sid, ()))
            for _ in range(max(0, room)):
                n = sum(have.get(t, 0) for t in wn) + 1
                best = max(sorted(wn), key=lambda t: wn[t] / wsum - have.get(t, 0) / n)
                if wn[best] <= 0 or USTATS[best].cost > pp:
                    break
                out.append(Build(st.sid, best))
                pp -= USTATS[best].cost
                have[best] = have.get(best, 0) + 1
        return out

    # -- engineers ----------------------------------------------------------

    def _fob_site(self, obs: Observation, toward: Coord) -> Optional[Coord]:
        side = obs.side
        hq = obs.hq[side]
        f = self.genome["fob_forward"]
        point = (hq[0] + (toward[0] - hq[0]) * f, hq[1] + (toward[1] - hq[1]) * f)
        point = (int(round(point[0])), int(round(point[1])))
        bases = [s.coord for s in obs.own_structures if s.kind in (ST.HQ, ST.FOB)]
        bases += list(obs.building)
        cands = [c for c in obs.objectives
                 if obs.objective_holder(c) == side and obs.map.passable(c, Domain.LAND)
                 and obs.structure_at(c) is None
                 and all(distance(c, b) >= P.TEMP_FOB_SPACING for b in bases)]
        return min(cands, key=lambda c: (distance(c, point), c), default=None)

    def _engineer(self, obs: Observation, unit, toward: Coord) -> Optional[Tuple[Order, str]]:
        if isinstance(unit.order, Construct):
            return unit.order, f"build FOB {unit.order.coord[0]},{unit.order.coord[1]}"
        n_fobs = len(obs.structures_of(obs.side, ST.FOB)) + len(obs.building)
        if n_fobs < P.TEMP_MAX_FOBS and obs.pp >= P.ECONOMY.fob_cost:
            site = self._fob_site(obs, toward)
            if site is not None:
                if distance(unit.coord, site) > 1:
                    return MoveTo(unit.uid, site), f"to FOB site {site[0]},{site[1]}"
                return Construct(unit.uid, site, ST.FOB), f"build FOB {site[0]},{site[1]}"
        dmg = [s for s in obs.own_structures if s.strength < 60.0]
        if dmg:
            s = min(dmg, key=lambda s: (distance(unit.coord, s.coord), s.sid))
            if distance(unit.coord, s.coord) <= 1:
                return Repair(unit.uid, s.sid), f"repair S{s.sid}"
            if obs.map.passable(s.coord, Domain.LAND):
                return MoveTo(unit.uid, s.coord), f"to repair S{s.sid}"
        return None

    # -- structure assault --------------------------------------------------

    def _attack_structure(self, obs: Observation, unit, tgt: Target) -> Optional[Order]:
        if tgt.sid is None:
            return None
        if any(e.coord == tgt.coord and e.domain is Domain.LAND for e in obs.enemy_units):
            return None
        if tgt.coord not in obs.visible_land:      # a defender could be there unseen: close in first
            return MoveTo(unit.uid, tgt.coord) if obs.map.passable(tgt.coord, unit.domain) else None
        return Attack(unit.uid, tgt.sid, structure=True)

    # -- main -----------------------------------------------------------------

    def act(self, obs: Observation) -> List[Order]:
        side = obs.side
        mine = obs.units_of(side)
        enemies = obs.units_of(side.other)
        self._tasks = {}
        self._observe(obs)
        if obs.turn > 0 and obs.turn % C.adapt_interval == 0:
            self._adapt(obs)
        builds = self._builds(obs)
        if not mine:
            return builds
        g = self.genome
        plan0 = Plan(main_effort=None,
                     enemy_air_cover=self._air_defence_coverage(obs, side.other),
                     own_air_cover=self._air_defence_coverage(obs, side),
                     ghosts=list(obs.ghosts))
        if enemies or plan0.ghosts:
            self._seen_enemy = True

        # -- efforts
        land = [u for u in mine if self._role(u) in ("infantry", "manoeuvre")]
        combat_land = [u for u in mine if u.domain is Domain.LAND and self._role(u) != "support"]
        main, second = self._pick(self._targets(obs, combat_land))
        main_coord = main.coord if main else obs.hq[side.other]
        threats = list(enemies) + list(plan0.ghosts)

        # -- garrisons and reserve (drawn from the infantry/armour units)
        post: Dict[int, Tuple[Coord, str]] = {}
        free = sorted(land, key=lambda u: u.uid)
        cap = int(len(land) * C.hold_back_frac)

        def take(n: int, at: Coord) -> List:
            got = sorted(free, key=lambda u: (distance(u.coord, at), u.uid))[:max(0, min(n, cap - len(post)))]
            for u in got:
                free.remove(u)
            return got

        worst: Optional[Tuple[int, Coord]] = None
        for s in sorted(obs.own_structures, key=lambda s: s.sid):
            wgt = self._defend_weight(s.kind)
            if wgt <= 0:
                continue
            near = sum(1 for e in threats if distance(e.coord, s.coord) <= C.garrison_radius)
            n = math.ceil(C.garrison_max * wgt) if near else (1 if wgt >= C.garrison_always else 0)
            if near and (worst is None or near > worst[0]):
                worst = (near, s.coord)
            for u in take(n, s.coord):
                post[u.uid] = (s.coord, f"garrison {s.kind.name}")
        rpost = worst[1] if worst else obs.hq[side]
        n_res = int(round(g.reserve * len(land)))
        for u in take(n_res, rpost):
            post[u.uid] = (rpost, "reserve")

        # -- split the remaining strike force between main and secondary effort
        sec_uids: Set[int] = set()
        if second is not None and main is not None and len(free) >= C.secondary_min_force:
            n_sec = max(1, int(round((1.0 - g.concentration) * len(free))))
            for u in sorted(free, key=lambda u: (distance(u.coord, second.coord), u.uid))[:n_sec]:
                sec_uids.add(u.uid)

        plan_main = replace(plan0, main_effort=main_coord, claimed=set())
        plan_sec = replace(plan_main, main_effort=second.coord) if second else plan_main
        orders: Dict[int, Order] = {}
        for unit in mine:
            o, task = self._unit_order(obs, side, unit, enemies, plan_main, plan_sec, main, second,
                                       post, sec_uids, main_coord)
            orders[unit.uid] = o
            self._tasks[unit.uid] = task

        # -- CP priority: new tasks, then units without a valid standing order, then the rest
        def rank(uid: int) -> int:
            u = next(x for x in mine if x.uid == uid)
            if self._last_task.get(uid) != self._tasks[uid]:
                return 0
            return 1 if u.order is None else 2
        ordered = sorted(orders, key=lambda uid: (rank(uid), uid))
        first = [orders[u] for u in ordered if rank(u) < 2]
        rest = [orders[u] for u in ordered if rank(u) == 2]
        self._last_task = dict(self._tasks)
        return first + builds + rest

    def _unit_order(self, obs, side, unit, enemies, plan_main, plan_sec, main, second,
                    post, sec_uids, main_coord) -> Tuple[Order, str]:
        g = self.genome
        role = self._role(unit)
        if self._needs_resupply(unit):
            step = self._resupply_move(obs, side, unit)
            if step is not None and step != unit.coord:
                return MoveTo(unit.uid, step), "resupply"
        if unit.stats.builds:
            r = self._engineer(obs, unit, main_coord)
            if r is not None:
                return r
        if role == "air":
            return self._air_task(obs, side, unit, enemies, plan_main, main_coord)
        if unit.uid in post:
            at, label = post[unit.uid]
            if any(distance(e.coord, unit.coord) <= 2 for e in enemies):
                return getattr(self, f"_{role}")(obs, side, unit, enemies,
                                                 replace(plan_main, main_effort=at)), label + " (engaged)"
            if unit.coord == at:
                return Hold(unit.uid), label
            if obs.map.passable(at, unit.domain):
                return MoveTo(unit.uid, at), label
            return Hold(unit.uid), label
        in_sec = unit.uid in sec_uids and second is not None
        tgt, plan = (second, plan_sec) if in_sec else (main, plan_main)
        label = f"attack {tgt.label}" if tgt else "advance"
        if g["expansion"] > 0.2:
            exp = self._expand(obs, side, unit, enemies, plan_main)
            if exp is not None:
                return exp, ("raid" if isinstance(exp, (Attack, Strike)) else "expand")
        if tgt is not None and tgt.sid is not None and role in ("infantry", "manoeuvre"):
            if not any(distance(e.coord, unit.coord) <= 4 for e in enemies):
                so = self._attack_structure(obs, unit, tgt)
                if so is not None:
                    return so, label
        handler = getattr(self, f"_{role}")
        return handler(obs, side, unit, enemies, plan), (label if role != "support" else "support")

    def _air_task(self, obs, side, unit, enemies, plan, main_coord) -> Tuple[Order, str]:
        if self.genome["air_role"] >= 0.5:
            return self._air(obs, side, unit, enemies, plan), "air strike"
        guard = [s.coord for s in obs.own_structures] + [u.coord for u in obs.own_units
                                                         if u.domain is Domain.LAND]
        threat = [e for e in enemies if any(distance(e.coord, c) <= C.garrison_radius for c in guard)]
        if threat:
            return self._air(obs, side, unit, enemies, plan), "air cover"
        home = obs.hq[side]
        if distance(unit.coord, home) <= 1:
            return Hold(unit.uid), "air cover (idle)"
        return MoveTo(unit.uid, home), "air cover (home)"


# The commander with the default genome is the "doctrine" anchor (PLAN M7).
from . import REGISTRY  # noqa: E402
REGISTRY["doctrine"] = Commander
