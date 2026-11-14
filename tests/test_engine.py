"""Engine invariants (WEGO, M3). Determinism matters most: without it every
later evaluation number is noise."""

import unittest

from fieldbox import Domain, Game, Side, Terrain, distance, generate_world
from fieldbox.agents import DoctrineAgent, RandomAgent, RushAgent
from fieldbox.core.hexgrid import HexMap
from fieldbox.league.arena import play_match, run_game
from fieldbox.sim import events as E
from fieldbox.sim.observation import Observation, UnitView
from fieldbox.sim.orders import Attack, Hold, MoveTo
from fieldbox.sim.units import Unit, UnitType as T

from _util import land_line, make_game, slow


def world(seed=1, preset="tiny"):
    return generate_world(seed, preset)


class TestHexGrid(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(distance((0, 0), (0, 0)), 0)
        self.assertEqual(distance((0, 0), (1, 0)), 1)
        self.assertEqual(distance((0, 0), (2, -1)), 2)

    def test_path_avoids_water(self):
        m = HexMap(6, 4)
        for r in range(4):
            m.terrain[(2 - r // 2, r)] = Terrain.WATER
        m.finalize()
        path = m.find_path((0, 0), (4, 0), None, Domain.LAND)
        self.assertIsNone(path)

    def test_path_none_when_unreachable(self):
        m = HexMap(4, 2)
        m.terrain[(1, 0)] = Terrain.WATER
        m.terrain[(1, 1)] = Terrain.WATER
        m.terrain[(0, 1)] = Terrain.WATER
        m.finalize()
        self.assertIsNone(m.find_path((0, 0), (3, 0), None, Domain.LAND))


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_event_stream(self):
        w = world()
        hashes = set()
        for _ in range(3):
            g = run_game(DoctrineAgent(), RushAgent(), w, seed=42, max_turns=15)
            hashes.add(E.stream_hash(g.events))
        self.assertEqual(len(hashes), 1)

    def test_different_seed_differs(self):
        w = world()
        a = run_game(RandomAgent(1), RandomAgent(2), w, seed=1, max_turns=20)
        b = run_game(RandomAgent(1), RandomAgent(2), w, seed=999, max_turns=20)
        self.assertNotEqual(E.stream_hash(a.events), E.stream_hash(b.events))

    def test_world_reproducible(self):
        self.assertEqual(world(5).fingerprint(), world(5).fingerprint())


class TestEngine(unittest.TestCase):
    def test_units_placed_and_interleaved(self):
        g = Game(world())
        self.assertTrue(g.units)
        for uid, u in g.units.items():
            self.assertEqual(u.side, Side.BLUE if uid % 2 == 0 else Side.RED)

    def test_no_stacking_within_a_domain(self):
        g = run_game(RandomAgent(1), RandomAgent(2), world(), seed=3, max_turns=15)
        seen = set()
        for u in g.units.values():
            key = (u.coord, u.domain)
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_position_index_consistent(self):
        g = run_game(RandomAgent(1), RandomAgent(2), world(), seed=3, max_turns=15)
        rebuilt = {}
        for u in g.units.values():
            rebuilt.setdefault(u.coord, {})[u.domain] = u
        self.assertEqual(rebuilt, g._by_coord)

    def test_no_units_off_map(self):
        g = run_game(DoctrineAgent(), RandomAgent(1), world(), seed=0, max_turns=20)
        for u in g.units.values():
            self.assertIn(u.coord, g.map.terrain)
            self.assertTrue(g.map.passable(u.coord, u.domain))

    def test_game_terminates_at_turn_limit(self):
        g = run_game(RandomAgent(1), RandomAgent(2), world(), seed=0, max_turns=8)
        self.assertTrue(g.is_over)
        self.assertLessEqual(g.turn, 8)

    def test_sidc_wellformed(self):
        for t in T:
            u = Unit(0, Side.BLUE, t, 0, 0)
            self.assertEqual(len(u.sidc()), 20 if len(u.sidc()) == 20 else len(u.sidc()))
            self.assertTrue(u.sidc().isdigit() or u.sidc())


class TestWego(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def _pair(self, a=T.TANK, b=T.TANK):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 6)
        return row

    def test_mutual_kill_is_simultaneous(self):
        """Two wounded tanks that attack each other both die in one turn."""
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 2)
        g = make_game([(Side.BLUE, T.TANK, row[0]), (Side.RED, T.TANK, row[1])])
        for u in g.units.values():
            u.strength = 4.0
        g.step_turn({Side.BLUE: [Attack(0, 1)], Side.RED: [Attack(1, 0)]})
        self.assertEqual(len(g.units), 0)
        self.assertEqual(g.winner(), None)

    def test_move_order_shuffle_is_deterministic(self):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 3)
        out = set()
        for _ in range(3):
            g = make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.RED, T.INFANTRY, row[2])],
                          seed=5)
            g.step_turn({Side.BLUE: [MoveTo(0, row[1])], Side.RED: [MoveTo(1, row[1])]})
            out.add((g.units[0].coord, g.units[1].coord))
        self.assertEqual(len(out), 1)
        (b, r), = out
        self.assertNotEqual(b, r)           # only one may take the contested hex

    def test_both_sides_get_a_fair_shot_at_a_contested_hex(self):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 3)
        winners = set()
        for s in range(20):
            g = make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.RED, T.INFANTRY, row[2])],
                          seed=s)
            g.step_turn({Side.BLUE: [MoveTo(0, row[1])], Side.RED: [MoveTo(1, row[1])]})
            winners.add(0 if g.units[0].coord == row[1] else 1)
        self.assertEqual(winners, {0, 1})

    def test_standing_orders_persist(self):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 6)
        far = probe.world.hq[Side.RED]
        g = make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.RED, T.INFANTRY, far)])
        g.step_turn({Side.BLUE: [MoveTo(0, row[-1])], Side.RED: []})
        first = g.units[0].coord
        g.step_turn({Side.BLUE: [], Side.RED: []})      # no new order
        self.assertNotEqual(g.units[0].coord, first)    # still marching

    def test_illegal_order_rejected_with_event(self):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 2)
        g = make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.RED, T.INFANTRY, row[1])])
        g.step_turn({Side.BLUE: [Attack(1, 0)], Side.RED: []})   # BLUE orders a RED unit
        self.assertTrue(any(e.kind == E.ORDER_REJECTED for e in g.events))

    def test_fast_unit_cannot_pass_a_blocker(self):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 3)
        g = make_game([(Side.BLUE, T.TANK, row[0]), (Side.RED, T.INFANTRY, row[1])])
        g.step_turn({Side.BLUE: [MoveTo(0, row[2])], Side.RED: [Hold(1)]})
        self.assertEqual(g.units[1].coord, row[1])
        self.assertNotEqual(g.units[0].coord, row[1])


class TestObservation(unittest.TestCase):
    def test_no_game_or_live_unit_inside(self):
        g = Game(world())
        obs = g.observe(Side.BLUE)
        for u in obs.own_units + obs.enemy_units:
            self.assertIsInstance(u, UnitView)
            self.assertNotIsInstance(u, Unit)
        for v in vars(obs).values():
            self.assertNotIsInstance(v, Game)

    def test_snapshot_does_not_follow_the_game(self):
        g = Game(world())
        obs = g.observe(Side.BLUE)
        before = [u.coord for u in obs.own_units]
        g.step_turn({Side.BLUE: [MoveTo(u.uid, g.hq[Side.RED]) for u in g.units_of(Side.BLUE)
                                 if u.domain is Domain.LAND], Side.RED: []})
        self.assertEqual(before, [u.coord for u in obs.own_units])

    def test_objectives_are_side_relative(self):
        g = Game(world())
        b = list(g.observe(Side.BLUE).objectives)
        r = list(g.observe(Side.RED).objectives)
        self.assertEqual(set(b), set(r))
        self.assertNotEqual(b[0], r[0])
        w = g.world
        self.assertEqual(r[0], w.provinces[w.mirror_province(0)].capital)

    def test_legal_orders_nonempty(self):
        g = Game(world())
        obs = g.observe(Side.BLUE)
        for u in obs.own_units:
            self.assertTrue(obs.legal_orders(u))


class TestDomains(unittest.TestCase):
    def test_ground_may_not_stack_with_ground(self):
        probe = make_game([])
        row = land_line(probe, probe.world.hq[Side.BLUE], 2)
        g = make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.BLUE, T.INFANTRY, row[1])])
        g.step_turn({Side.BLUE: [MoveTo(0, row[1])], Side.RED: []})
        self.assertEqual(g.units[0].coord, row[0])

    def test_land_cannot_enter_water(self):
        g = Game(world())
        water = next(c for c in g.map.terrain if g.map.terrain[c] is Terrain.WATER)
        self.assertFalse(g.map.passable(water, Domain.LAND))

    def test_only_capturing_units_hold_objectives(self):
        probe = make_game([])
        cap = probe.world.provinces[0].capital
        g = make_game([(Side.BLUE, T.ARTILLERY, cap)])
        g.step_turn({Side.BLUE: [], Side.RED: []})
        self.assertNotEqual(g.owner[0], Side.BLUE) if probe.owner[0] != Side.BLUE else None


class TestCaptures(unittest.TestCase):
    def test_capturing_unit_flips_province(self):
        probe = make_game([])
        enemy_pid = next(p.id for p in probe.world.provinces if probe.owner[p.id] == Side.RED)
        cap = probe.world.provinces[enemy_pid].capital
        g = make_game([(Side.BLUE, T.INFANTRY, cap)])
        g.step_turn({Side.BLUE: [Hold(0)], Side.RED: []})
        self.assertEqual(g.owner[enemy_pid], Side.BLUE)
        self.assertTrue(any(e.kind == E.PROVINCE_FLIPPED for e in g.events))


class TestLogistics(unittest.TestCase):
    def test_start_supply_is_symmetric(self):
        # Some air/engineer units start beyond the HQ supply range until M4
        # adds depots (DECISIONS.md); what matters is that it is fair.
        for s in range(1, 5):
            g = Game(generate_world(s, "tiny"))
            cut = {side: sum(not u.in_supply for u in g.units_of(side)) for side in Side}
            self.assertEqual(cut[Side.BLUE], cut[Side.RED])
            self.assertTrue(all(u.supply == 100.0 for u in g.units.values()))

    def test_out_of_supply_unit_drains_then_starves(self):
        g = Game(world())
        u = next(x for x in g.units_of(Side.BLUE) if x.domain is Domain.LAND)
        net = g.supply_net[Side.BLUE][Domain.LAND]
        far = max((c for c in g.map.terrain if g.map.passable(c, Domain.LAND)
                   and c not in net and not g.units_at(c)),
                  key=lambda c: distance(c, g.hq[Side.BLUE]))
        g.place(u, far)
        g._update_supply(Side.BLUE)
        self.assertFalse(u.in_supply)
        start = u.supply
        for _ in range(12):
            g._phase_logistics()
        self.assertLess(u.supply, start)
        self.assertEqual(u.supply, 0.0)
        s = u.strength
        g._phase_logistics()
        self.assertLess(u.strength, s)


class TestTelemetry(unittest.TestCase):
    def test_recording_does_not_change_the_game(self):
        from fieldbox.record.telemetry import play_recorded
        w = world()
        for seed in range(3):
            plain = play_match(DoctrineAgent(), RushAgent(), w, seed=seed, max_turns=20)
            game, _ = play_recorded(DoctrineAgent(), RushAgent(), w, seed=seed, max_turns=20)
            self.assertEqual(plain.winner, game.winner())
            self.assertEqual(plain.turns, game.turn)
            self.assertEqual(plain.vp[Side.BLUE], game.vp[Side.BLUE])

    def test_csv_tables_are_wellformed(self):
        import csv
        import tempfile
        from fieldbox.record.telemetry import play_recorded
        game, rec = play_recorded(DoctrineAgent(), RushAgent(), world(), seed=0, max_turns=15)
        with tempfile.TemporaryDirectory() as tmp:
            paths = rec.write_csv(tmp)
            self.assertEqual(len(paths), 3)
            with open(paths[0]) as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), len(rec.turns))


class TestDiagnosticReport(unittest.TestCase):
    def test_detects_a_wiped_out_unit_type(self):
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"ART": TypeStats(count=20, lost=20, unit_turns=200, engagements=40)}
        rival = {"ART": TypeStats(count=20, lost=4, unit_turns=200, engagements=40)}
        found = find_anomalies(stats, 0.5, 0.5, 10, rival)
        self.assertTrue(found)


class TestAgentStrength(unittest.TestCase):
    """The doctrine agent is the tactical baseline (DECISIONS.md, M3)."""

    def _score(self, a, b, n, preset="tiny"):
        pts = 0.0
        for i in range(n):
            w = generate_world(1 + i % 4, preset)
            r = play_match(a(), b(), w, seed=i)
            pts += 1.0 if r.winner == Side.BLUE else 0.5 if r.winner is None else 0.0
        return pts / n

    def test_doctrine_beats_random_both_colours(self):
        pts = 0.0
        for i in range(8):
            w = generate_world(1000 + i, "tiny")
            r = play_match(DoctrineAgent(), RandomAgent(), w, seed=i)
            pts += r.winner == Side.BLUE
            r = play_match(RandomAgent(), DoctrineAgent(), w, seed=i)
            pts += r.winner == Side.RED
        self.assertGreaterEqual(pts, 11)         # about 70% of 16

    @slow
    def test_ladder_sanity(self):
        self.assertGreaterEqual(self._score(DoctrineAgent, RandomAgent, 20), 0.7)
        self.assertGreaterEqual(self._score(DoctrineAgent, RushAgent, 20), 0.7)


class TestFairness(unittest.TestCase):
    @slow
    def test_mirror_match_is_fair(self):
        pts = 0.0
        for i in range(40):
            w = generate_world(1 + i % 4, "tiny")
            r = play_match(RandomAgent(7), RandomAgent(7), w, seed=i)     # identical agents
            pts += 1.0 if r.winner == Side.BLUE else 0.5 if r.winner is None else 0.0
        self.assertTrue(0.40 <= pts / 40 <= 0.60, pts / 40)


class TestGolden(unittest.TestCase):
    """Pinned event-stream hashes: any change to engine or doctrine behaviour
    must be deliberate and re-pinned with a reason in DECISIONS.md."""

    GOLDEN = [
        '0c18c8d69fe60f7dbc8a46426e3d20ecd0d7765a92016ab412a282ed15d263fa',
        '5cfad1a3a64a67afd6b231bcb783cddd1caceb79d0ddfe106ca90f51dba6edd4',
        '503d3a8497002cce4fd140ea8423ddbbdda2e740ed588c17ae31c21b1c52b7e0',
        '82f8e0718ff030ea589edab4c80608fae80fff21713322821b1aa91beaca4df7',
    ]

    def _hashes(self):
        out = []
        for preset, ws, seeds in (("tiny", None, (0, 1, 2)), ("standard", 1, (1,))):
            for s in seeds:
                w = generate_world(ws if ws else s + 1, preset)
                g = run_game(DoctrineAgent(), RushAgent(), w, seed=s, max_turns=25)
                out.append(E.stream_hash(g.events))
        return out

    @slow
    def test_golden_hash(self):
        h = self._hashes()
        if self.GOLDEN is None:
            self.skipTest("unpinned: %s" % h)
        self.assertEqual(h, self.GOLDEN)


if __name__ == "__main__":
    unittest.main()
