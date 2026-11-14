"""M4: structures, power, production, command points, supply sources, victory."""

import unittest

from fieldbox import Domain, Game, Side, distance, generate_world
from fieldbox.agents import DoctrineAgent, RandomAgent, RushAgent
from fieldbox.league.arena import run_game
from fieldbox.sim import events as E
from fieldbox.sim.orders import Attack, Bombard, Build, Construct, Hold, MoveTo, Repair, Strike
from fieldbox.sim.structures import StructureType as ST
from fieldbox.sim.units import UnitType as T

from _util import land_line, make_game, slow


def blank(**kw):
    """A game with no units; structures from the generated map."""
    return make_game([], **kw)


def free_land_near(g, coord, n=1, side=None):
    out = []
    for d in range(1, 8):
        for c in g._ring(coord, d):
            if g.map.passable(c, Domain.LAND) and not g.units_at(c) and c not in g._struct_at:
                out.append(c)
    return out[:n]


class TestStructures(unittest.TestCase):
    def test_both_sides_start_with_mirrored_structures(self):
        g = Game(generate_world(1, "tiny"))
        b = sorted(s.kind.name for s in g.structures_of(Side.BLUE))
        r = sorted(s.kind.name for s in g.structures_of(Side.RED))
        self.assertEqual(b, r)
        self.assertIn("HQ", b)

    def test_power_factor_in_unit_interval_and_full_at_start(self):
        g = Game(generate_world(1, "tiny"))
        for s in Side:
            self.assertEqual(g.power[s], 1.0)
        for p in g.structures_of(Side.BLUE, ST.POWER):
            p.strength = 10.0
        pf = g._power_factor(Side.BLUE)
        self.assertTrue(0.0 <= pf < 1.0)
        for p in g.structures_of(Side.BLUE, ST.POWER):
            p.strength = 0.0
        self.assertEqual(g._power_factor(Side.BLUE), 0.0)

    def test_lost_power_cuts_income_and_cp(self):
        g = blank()
        cp_full = g.cp(Side.BLUE)
        for p in g.structures_of(Side.BLUE, ST.POWER):
            p.strength = 0.0
        g.power[Side.BLUE] = g._power_factor(Side.BLUE)
        self.assertLess(g.cp(Side.BLUE), cp_full)
        self.assertEqual(g.cp(Side.BLUE), 4)          # cp_base only

    def test_no_data_center_leaves_cp_base(self):
        g = blank()
        for d in g.structures_of(Side.BLUE, ST.DATACENTER):
            d.strength = 0.0
        self.assertEqual(g.cp(Side.BLUE), 4)


class TestEconomy(unittest.TestCase):
    def test_income_adds_pp_and_never_negative(self):
        g = blank(max_turns=50)
        before = g.pp[Side.BLUE]
        g.step_turn({})
        self.assertGreater(g.pp[Side.BLUE], before)
        for _ in range(5):
            g.step_turn({})
        self.assertTrue(all(v >= 0 for v in g.pp.values()))

    def test_build_costs_pp_up_front_and_spawns_after_build_turns(self):
        g = blank(max_turns=50)
        hq = next(iter(g.structures_of(Side.BLUE, ST.HQ)))
        pp0 = g.pp[Side.BLUE]
        g.step_turn({Side.BLUE: [Build(hq.sid, T.INFANTRY)]})
        self.assertEqual(len(g.units_of(Side.BLUE)), 0)
        income_1 = g.pp[Side.BLUE] - (pp0 - 20)
        g.step_turn({})
        self.assertEqual(len(g.units_of(Side.BLUE)), 1)
        self.assertGreater(income_1, 0)
        self.assertTrue(any(e.kind == E.UNIT_BUILT for e in g.events))

    def test_build_rejected_when_short_of_pp_or_wrong_producer(self):
        g = blank()
        hq = next(iter(g.structures_of(Side.BLUE, ST.HQ)))
        g.pp[Side.BLUE] = 10.0
        g.step_turn({Side.BLUE: [Build(hq.sid, T.INFANTRY)]})
        g.step_turn({Side.BLUE: [Build(hq.sid, T.JET)]})        # HQ builds land only
        reasons = [e.data["reason"] for e in g.events if e.kind == E.ORDER_REJECTED]
        self.assertIn("not enough PP", reasons)
        self.assertIn("structure cannot build that", reasons)

    def test_queue_depth_is_capped(self):
        g = blank()
        g.pp[Side.BLUE] = 1000.0
        hq = next(iter(g.structures_of(Side.BLUE, ST.HQ)))
        g.step_turn({Side.BLUE: [Build(hq.sid, T.INFANTRY)] * 5})
        self.assertLessEqual(len(g.queues[hq.sid]), 3)
        self.assertTrue(any(e.data.get("reason") == "queue full"
                            for e in g.events if e.kind == E.ORDER_REJECTED))

    def test_region_bonus_adds_pp(self):
        g = blank()
        rg = g.world.regions[0]
        base = g._region_bonus(Side.BLUE)
        for pid in rg.provinces:
            g.owner[pid] = Side.BLUE
        self.assertGreaterEqual(g._region_bonus(Side.BLUE), base + rg.bonus - base)
        self.assertGreaterEqual(g._region_bonus(Side.BLUE), rg.bonus)


class TestCommandPoints(unittest.TestCase):
    def _many(self):
        probe = blank()
        cells = free_land_near(probe, probe.world.hq[Side.BLUE], 14)
        units = [(Side.BLUE, T.INFANTRY, c) for c in cells]
        units.append((Side.RED, T.INFANTRY, probe.world.hq[Side.RED]))
        return make_game(units, max_turns=50), len(cells)

    def test_cp_cap_drops_surplus_orders_and_logs_it(self):
        g, n = self._many()
        cp = g.cp(Side.BLUE)
        self.assertLess(cp, n)
        far = g.world.hq[Side.RED]
        g.step_turn({Side.BLUE: [MoveTo(u.uid, far) for u in g.units_of(Side.BLUE)]})
        moving = [u for u in g.units_of(Side.BLUE) if u.uid in g.standing]
        self.assertEqual(len(moving), cp)
        cap = [e for e in g.events if e.kind == E.ORDERS_CAPPED]
        self.assertEqual(cap[0].data["dropped"], n - cp)

    def test_cap_keeps_the_first_orders_in_list_order(self):
        g, n = self._many()
        cp = g.cp(Side.BLUE)
        far = g.world.hq[Side.RED]
        ids = [u.uid for u in g.units_of(Side.BLUE)]
        g.step_turn({Side.BLUE: [MoveTo(i, far) for i in reversed(ids)]})
        self.assertEqual(set(g.standing), set(list(reversed(ids))[:cp]))

    def test_reissuing_a_standing_order_costs_no_cp(self):
        g, n = self._many()
        far = g.world.hq[Side.RED]
        orders = [MoveTo(u.uid, far) for u in g.units_of(Side.BLUE)]
        g.step_turn({Side.BLUE: orders})
        first = set(g.standing)
        g.step_turn({Side.BLUE: [o for o in orders if o.uid in first]})
        self.assertFalse(any(e.kind == E.ORDERS_CAPPED and e.turn == 1 for e in g.events))

    def test_build_orders_cost_no_cp(self):
        g, n = self._many()
        g.pp[Side.BLUE] = 500.0
        hq = next(iter(g.structures_of(Side.BLUE, ST.HQ)))
        g.step_turn({Side.BLUE: [Build(hq.sid, T.INFANTRY)] * 3})
        self.assertFalse(any(e.kind == E.ORDERS_CAPPED for e in g.events))


class TestStructureCombat(unittest.TestCase):
    def _setup(self, utype=T.TANK, kind=ST.INDUSTRY):
        probe = blank()
        st = probe.structures_of(Side.RED, kind)[0]
        near = free_land_near(probe, st.coord, 1)[0]
        g = make_game([(Side.BLUE, utype, near), (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])],
                      max_turns=50)
        return g, g.structures_of(Side.RED, kind)[0], near

    def test_assault_damages_a_structure_without_return_fire(self):
        g, st, near = self._setup()
        # put the attacker adjacent
        adj = [c for c in g._ring(st.coord, 1) if g.map.passable(c, Domain.LAND) and not g.units_at(c)][0]
        g.place(g.units[0], adj)
        g.step_turn({Side.BLUE: [Attack(0, st.sid, structure=True)]})
        self.assertLess(st.strength, 100.0)
        self.assertEqual(g.units[0].strength, 100.0)
        self.assertTrue(any(e.kind == E.STRUCTURE_DAMAGED for e in g.events))

    def test_destroyed_structure_is_removed_and_scores_vp(self):
        g, st, near = self._setup()
        adj = [c for c in g._ring(st.coord, 1) if g.map.passable(c, Domain.LAND) and not g.units_at(c)][0]
        g.place(g.units[0], adj)
        st.strength = 1.0
        vp0 = g.vp[Side.BLUE]
        g.step_turn({Side.BLUE: [Attack(0, st.sid, structure=True)]})
        self.assertNotIn(st.sid, g.structures)
        self.assertIsNone(g.structure_at(st.coord))
        self.assertGreaterEqual(g.vp[Side.BLUE] - vp0, 6)       # INDUSTRY structure_vp

    def test_cannot_assault_a_defended_structure(self):
        g, st, near = self._setup()
        # the RED infantry stands on the structure hex
        g.place(g.units[1], st.coord)
        adj = [c for c in g._ring(st.coord, 1) if g.map.passable(c, Domain.LAND) and not g.units_at(c)][0]
        g.place(g.units[0], adj)
        g.step_turn({Side.BLUE: [Attack(0, st.sid, structure=True)]})
        self.assertTrue(any(e.data.get("reason") == "structure is defended"
                            for e in g.events if e.kind == E.ORDER_REJECTED))

    def test_air_strike_hits_a_structure_and_only_air_may_strike(self):
        g, st, near = self._setup(T.JET)
        g.step_turn({Side.BLUE: [Strike(0, st.sid)]})
        self.assertLess(st.strength, 100.0)
        g2, st2, _ = self._setup(T.TANK)
        g2.step_turn({Side.BLUE: [Strike(0, st2.sid)]})
        self.assertTrue(any(e.kind == E.ORDER_REJECTED for e in g2.events))

    def test_artillery_bombards_a_structure_in_reach(self):
        g, st, near = self._setup(T.ARTILLERY)
        spot = [c for c in g._ring(st.coord, 2) if g.map.passable(c, Domain.LAND) and not g.units_at(c)][0]
        g.place(g.units[0], spot)
        g.step_turn({Side.BLUE: [Bombard(0, st.coord)]})
        self.assertLess(st.strength, 100.0)

    def test_capture_flips_a_capturable_structure_and_caps_strength(self):
        g, st, near = self._setup(T.INFANTRY, ST.AIRFIELD)
        g.place(g.units[0], st.coord)
        g.step_turn({Side.BLUE: [Hold(0)]})
        self.assertIs(st.side, Side.BLUE)
        self.assertLessEqual(st.strength, 50.0)
        self.assertTrue(any(e.kind == E.STRUCTURE_CAPTURED for e in g.events))

    def test_hq_cannot_be_captured(self):
        g, st, near = self._setup(T.INFANTRY, ST.HQ)
        g.place(g.units[0], st.coord)
        g.step_turn({Side.BLUE: [Hold(0)]})
        self.assertIs(st.side, Side.RED)

    def test_structure_repairs_in_supply_when_not_attacked(self):
        g, st, near = self._setup(T.INFANTRY, ST.RADAR)
        st.strength = 50.0
        g._update_supply(Side.RED)
        in_net = st.coord in g.supply_net[Side.RED][Domain.LAND]
        g.step_turn({})
        if in_net:
            self.assertGreater(st.strength, 50.0)

    def test_engineer_repair_adds_strength(self):
        probe = blank()
        st = probe.structures_of(Side.BLUE, ST.RADAR)[0]
        spot = free_land_near(probe, st.coord, 1)[0]
        g = make_game([(Side.BLUE, T.ENGINEER, spot), (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])])
        st = g.structures_of(Side.BLUE, ST.RADAR)[0]
        g.place(g.units[0], [c for c in g._ring(st.coord, 1) if g.map.passable(c, Domain.LAND)][0])
        st.strength = 30.0
        g.step_turn({Side.BLUE: [Repair(0, st.sid)]})
        self.assertGreaterEqual(st.strength, 30.0 + 10.0)


class TestConstruction(unittest.TestCase):
    def test_engineer_builds_a_fob_over_the_build_time(self):
        probe = blank()
        spot = free_land_near(probe, probe.world.hq[Side.BLUE], 3)[-1]
        g = make_game([(Side.BLUE, T.ENGINEER, spot), (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])],
                      max_turns=50)
        pp0 = g.pp[Side.BLUE]
        g.step_turn({Side.BLUE: [Construct(0, spot, ST.FOB)]})
        self.assertLess(g.pp[Side.BLUE], pp0 + 100 - 39)         # paid fob_cost up front
        self.assertEqual(len(g.structures_of(Side.BLUE, ST.FOB)), 0)
        for _ in range(3):
            g.step_turn({})
        self.assertEqual(len(g.structures_of(Side.BLUE, ST.FOB)), 1)
        self.assertTrue(any(e.kind == E.FOB_BUILT for e in g.events))

    def test_construction_cancelled_if_the_engineer_dies(self):
        probe = blank()
        spot = free_land_near(probe, probe.world.hq[Side.BLUE], 3)[-1]
        g = make_game([(Side.BLUE, T.ENGINEER, spot), (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])],
                      max_turns=50)
        g.step_turn({Side.BLUE: [Construct(0, spot, ST.FOB)]})
        g._kill(g.units[0], "test")
        for _ in range(4):
            g.step_turn({})
        self.assertEqual(len(g.structures_of(Side.BLUE, ST.FOB)), 0)

    def test_construct_rejected_without_pp_or_for_non_engineers(self):
        probe = blank()
        spot = free_land_near(probe, probe.world.hq[Side.BLUE], 3)[-1]
        g = make_game([(Side.BLUE, T.ENGINEER, spot), (Side.BLUE, T.INFANTRY, free_land_near(probe, spot, 2)[1]),
                       (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])])
        g.pp[Side.BLUE] = 5.0
        g.step_turn({Side.BLUE: [Construct(0, spot, ST.FOB), Construct(1, spot, ST.FOB)]})
        reasons = [e.data["reason"] for e in g.events if e.kind == E.ORDER_REJECTED]
        self.assertIn("not enough PP", reasons)
        self.assertIn("cannot build", reasons)


class TestSupplySources(unittest.TestCase):
    def test_every_unit_starts_in_supply(self):
        for preset in ("tiny", "standard", "large"):
            for seed in (1, 2, 3):
                g = Game(generate_world(seed, preset))
                cut = [u for u in g.units.values() if not u.in_supply]
                self.assertEqual(cut, [], f"{preset} seed {seed}")

    def test_land_supply_does_not_cover_the_map(self):
        g = Game(generate_world(1, "standard"))
        land = [c for c in g.map.terrain if g.map.passable(c, Domain.LAND)]
        cov = len(g.supply_net[Side.BLUE][Domain.LAND]) / len(land)
        self.assertLess(cov, 0.75)

    def test_fob_extends_land_supply_and_rearms_aircraft(self):
        probe = blank()
        far = max((c for c in probe.map.terrain if probe.map.passable(c, Domain.LAND)
                   and c not in probe.supply_net[Side.BLUE][Domain.LAND]
                   and probe.world.half(c) is Side.BLUE),
                  key=lambda c: distance(c, probe.world.hq[Side.BLUE]))
        g = make_game([(Side.RED, T.INFANTRY, probe.world.hq[Side.RED])])
        self.assertNotIn(far, g.supply_net[Side.BLUE][Domain.LAND])
        sid = g._next_sid
        from fieldbox.sim.structures import Structure
        g.structures[sid] = Structure(sid, ST.FOB, Side.BLUE, far[0], far[1])
        g._struct_at[far] = g.structures[sid]
        g._update_supply(Side.BLUE)
        self.assertIn(far, g.supply_net[Side.BLUE][Domain.LAND])
        self.assertIn(far, g.supply_net[Side.BLUE][Domain.AIR])

    def test_losing_the_airfield_cuts_air_supply(self):
        g = make_game([(Side.RED, T.INFANTRY, generate_world(1, "tiny").hq[Side.RED])])
        for a in g.structures_of(Side.BLUE, ST.AIRFIELD):
            g._destroy_structure(a)
        g._update_supply(Side.BLUE)
        self.assertEqual(g.supply_net[Side.BLUE][Domain.AIR], set())


class TestVictory(unittest.TestCase):
    def test_hq_destruction_ends_the_game_for_the_owner(self):
        probe = blank()
        hq = probe.structures_of(Side.RED, ST.HQ)[0]
        near = free_land_near(probe, hq.coord, 1)[0]
        g = make_game([(Side.BLUE, T.TANK, near), (Side.RED, T.INFANTRY, free_land_near(probe, hq.coord, 5)[-1])])
        hq = g.structures_of(Side.RED, ST.HQ)[0]
        adj = [c for c in g._ring(hq.coord, 1) if g.map.passable(c, Domain.LAND) and not g.units_at(c)][0]
        g.place(g.units[0], adj)
        hq.strength = 1.0
        g.step_turn({Side.BLUE: [Attack(0, hq.sid, structure=True)]})
        self.assertTrue(g.is_over)
        self.assertEqual(g.result, (Side.BLUE, "hq_destroyed"))

    def test_both_hqs_destroyed_is_a_draw(self):
        g = blank()
        for s in Side:
            for h in g.structures_of(s, ST.HQ):
                g._destroy_structure(h)
        g.step_turn({})
        self.assertEqual(g.result, (None, "hq_destroyed"))

    def test_side_with_no_units_but_pp_does_not_lose(self):
        g = blank(max_turns=50)
        g.step_turn({})
        self.assertFalse(g.is_over)

    def test_annihilation_needs_no_units_no_queue_and_no_pp(self):
        g = blank(max_turns=50)
        self.assertTrue(g._can_fight(Side.BLUE))           # PP and producers: still alive
        g.pp[Side.BLUE] = 0.0
        self.assertFalse(g._can_fight(Side.BLUE))          # nothing left to build with
        g.pp[Side.BLUE] = 100.0
        for st in list(g.structures_of(Side.BLUE)):
            if st.stats.produces is not None:
                g._destroy_structure(st)
        self.assertFalse(g._can_fight(Side.BLUE))          # no producer
        hq = make_game([(Side.BLUE, T.INFANTRY, generate_world(1, "tiny").hq[Side.BLUE])])
        hq.pp[Side.BLUE] = 0.0
        self.assertTrue(hq._can_fight(Side.BLUE))          # a unit is enough

    def test_turn_limit_decides_on_vp(self):
        g = blank(max_turns=1)
        g.vp[Side.BLUE] = 5.0
        g.step_turn({})
        self.assertEqual(g.result[1], "turn_limit")
        self.assertEqual(g.result[0], Side.BLUE)


class TestInvariantsInPlay(unittest.TestCase):
    def test_economy_invariants_over_games(self):
        for a, b in ((DoctrineAgent(), RushAgent()), (RandomAgent(), DoctrineAgent())):
            g = Game(generate_world(2, "tiny"), seed=1)
            a.reset(Side.BLUE, g.world)
            b.reset(Side.RED, g.world)
            while not g.is_over:
                g.step_turn({Side.BLUE: a.act(g.observe(Side.BLUE)),
                             Side.RED: b.act(g.observe(Side.RED))})
                for s in Side:
                    self.assertGreaterEqual(g.pp[s], 0.0)
                    self.assertTrue(0.0 <= g.power[s] <= 1.0)
                seen = set()
                for u in g.units.values():
                    self.assertNotIn((u.coord, u.domain), seen)
                    seen.add((u.coord, u.domain))
                    self.assertTrue(g.map.passable(u.coord, u.domain))
                for st in g.structures.values():
                    self.assertGreater(st.strength, 0.0)
                    self.assertLessEqual(st.strength, 100.0)

    def test_observation_exposes_economy_without_the_game(self):
        g = Game(generate_world(1, "tiny"))
        obs = g.observe(Side.BLUE)
        self.assertEqual(obs.pp, 100.0)
        self.assertEqual(obs.cp, g.cp(Side.BLUE))
        self.assertTrue(obs.build_options())
        self.assertEqual({s.sid for s in obs.own_structures} & {s.sid for s in obs.enemy_structures}, set())
        from fieldbox.sim.structures import Structure
        for v in obs.own_structures + obs.enemy_structures:
            self.assertNotIsInstance(v, Structure)

    @slow
    def test_standard_game_with_economy_runs_and_is_deterministic(self):
        w = generate_world(1, "standard")
        h = []
        for _ in range(2):
            g = run_game(DoctrineAgent(), RushAgent(), w, seed=3, max_turns=25)
            h.append(E.stream_hash(g.events))
        self.assertEqual(h[0], h[1])


if __name__ == "__main__":
    unittest.main()
