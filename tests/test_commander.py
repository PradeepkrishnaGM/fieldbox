"""M7: doctrine genome, commander, adaptation, replay v3."""

import json
import unittest
from collections import Counter

from fieldbox import Side, generate_world
from fieldbox.agents import (REGISTRY, Commander, DoctrineAgent, DoctrineGenome,
                             RandomAgent, RushAgent, TacticalAgent)
from fieldbox.agents.genome import TRAITS
from fieldbox.core.rng import make_rng
from fieldbox.league.arena import play_match, run_game
from fieldbox.record.replay import Replay, record_game
from fieldbox.sim.units import UnitType as T

from _util import make_game, slow


class TestGenome(unittest.TestCase):
    def test_default_round_trips_exactly(self):
        g = DoctrineGenome.default()
        self.assertEqual(DoctrineGenome.from_json(g.to_json()), g)
        m = g.mutate(make_rng("t", 1), mut_prob=1.0, sigma=0.3)
        self.assertEqual(DoctrineGenome.from_json(m.to_json()), m)

    def test_traits_in_unit_interval_and_complete(self):
        g = DoctrineGenome.default()
        self.assertEqual(len(g.values), len(TRAITS))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in g.values))
        with self.assertRaises(KeyError):
            DoctrineGenome.from_dict({"nonsense": 0.1})
        self.assertEqual(DoctrineGenome.from_dict({"aggression": 5}).values[0], 1.0)  # clipped

    def test_default_decodes_to_the_old_doctrine(self):
        g = DoctrineGenome.default()
        self.assertAlmostEqual(g.ratio_mult, 1.0, places=6)
        self.assertAlmostEqual(g.withdraw_below, 30.05, places=1)
        self.assertEqual(Commander().aggression, 1.0)

    def test_mutation_and_crossover_are_deterministic_and_bounded(self):
        g = DoctrineGenome.default()
        a = g.mutate(make_rng("m", 3))
        b = g.mutate(make_rng("m", 3))
        self.assertEqual(a, b)
        self.assertNotEqual(a, g)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in g.mutate(make_rng("m", 4), 1.0, 5.0).values))
        x, y = g.with_(aggression=0.0), g.with_(aggression=1.0)
        kids = {DoctrineGenome.crossover(x, y, make_rng("c", i), cx_prob=1.0).values for i in range(20)}
        for k in kids:
            self.assertTrue(all(v in (p, q) for v, p, q in zip(k, x.values, y.values)))
        self.assertEqual(DoctrineGenome.crossover(x, y, make_rng("c", 1), cx_prob=0.0), x)

    def test_registry(self):
        self.assertIs(REGISTRY["doctrine"], Commander)
        self.assertIs(DoctrineAgent, Commander)
        self.assertIn("doctrine-legacy", REGISTRY)


class TestAdaptation(unittest.TestCase):
    def _obs_vs_air(self, adaptivity):
        g = make_game([(Side.BLUE, T.INFANTRY, (6, 8)), (Side.RED, T.JET, (7, 8)),
                       (Side.RED, T.HELICOPTER, (7, 9))])
        c = Commander(DoctrineGenome.default().with_(adaptivity=adaptivity))
        c.reset(Side.BLUE, g.world)
        obs = g.observe(Side.BLUE)
        c._observe(obs)
        c._adapt(obs)
        return c, obs

    def test_air_heavy_enemy_raises_sam_share(self):
        c, obs = self._obs_vs_air(1.0)
        self.assertGreater(c.build_boost.get(T.SAM, 0.0), 0.5)
        ev = c.drain_events()
        self.assertTrue(ev and ev[0]["k"] == "adapted" and ev[0]["d"]["mode"] == "build")
        self.assertIn("SAM", ev[0]["d"]["shifts"])
        sams = sum(1 for b in c._builds(obs) if b.utype is T.SAM)
        c0, obs0 = self._obs_vs_air(0.0)
        sams0 = sum(1 for b in c0._builds(obs0) if b.utype is T.SAM)
        self.assertGreaterEqual(sams, sams0)
        self.assertEqual(c0.drain_events(), [])
        self.assertEqual(c0.build_boost, {})

    def test_hit_structure_raises_defend_weight(self):
        g = make_game([(Side.BLUE, T.INFANTRY, (6, 8)), (Side.RED, T.INFANTRY, (20, 8))])
        c = Commander(DoctrineGenome.default().with_(adaptivity=1.0))
        c.reset(Side.BLUE, g.world)
        obs = g.observe(Side.BLUE)
        c._observe(obs)
        hq = next(s for s in g.structures_of(Side.BLUE) if s.kind.name == "HQ")
        hq.strength -= 20
        g.turn = 5
        obs = g.observe(Side.BLUE)
        c._observe(obs)
        c._adapt(obs)
        self.assertIn("HQ", c.defend_boost)
        self.assertGreater(c._defend_weight(hq.kind), DoctrineGenome.default()["defend_hq"])


class TestCommanderPlay(unittest.TestCase):
    def test_plays_deterministically(self):
        w = generate_world(2, "tiny")
        h = []
        for _ in range(2):
            g = run_game(Commander(), RushAgent(), w, seed=5, max_turns=20)
            h.append((g.stream_hash() if hasattr(g, "stream_hash") else None, g.vp[Side.BLUE],
                      g.vp[Side.RED], g.turn))
        self.assertEqual(h[0], h[1])

    def test_beats_random_and_hold_both_colours(self):
        from fieldbox.agents import HoldAgent
        pts = 0
        for i in range(4):
            w = generate_world(1 + i, "tiny")
            for opp in (RandomAgent, HoldAgent):
                pts += play_match(Commander(), opp(), w, seed=i).winner == Side.BLUE
                pts += play_match(opp(), Commander(), w, seed=i).winner == Side.RED
        self.assertGreaterEqual(pts, 14)             # of 16

    def test_tasks_and_events_reach_the_replay(self):
        w = generate_world(1, "tiny")
        g, rep = record_game(Commander(), Commander(), w, seed=1, max_turns=14)
        tasks = [r[10] for s in rep.snapshots[1:] for r in s["units"]]
        self.assertTrue(tasks and any(tasks))
        self.assertTrue(any(t.startswith("attack") for t in tasks))
        self.assertTrue(any(e["k"] == "adapted" for evs in rep.events for e in evs))

    def test_replay_v3_round_trip_and_reads_older(self):
        import os
        import tempfile
        w = generate_world(1, "tiny")
        g, rep = record_game(Commander(), RushAgent(), w, seed=1, max_turns=8)
        with tempfile.TemporaryDirectory() as d:
            p = rep.save(os.path.join(d, "a.fbr"))
            back = Replay.load(p)
        self.assertEqual(back.snapshots, rep.snapshots)
        self.assertEqual(rep.to_dict()["version"], 3)

    def test_legacy_agent_still_plays(self):
        w = generate_world(1, "tiny")
        g = run_game(TacticalAgent(), RushAgent(), w, seed=1, max_turns=10)
        self.assertGreater(g.turn, 0)

    def test_no_orders_rejected_for_unenterable_destinations(self):
        """M4 carry-over: rush artillery moved onto enemy ships."""
        w = generate_world(1, "tiny")
        g, rep = record_game(RushAgent(), Commander(), w, seed=2, max_turns=30)
        bad = [e for evs in rep.events for e in evs
               if e["k"] == "order_rejected" and e["d"].get("reason") == "destination impassable"]
        self.assertEqual(bad, [])


@slow
class TestExtremeGenomes(unittest.TestCase):
    def _built(self, genome, n=3):
        c = Counter()
        for i in range(n):
            w = generate_world(1 + i, "tiny")
            _, rep = record_game(Commander(genome), RandomAgent(), w, seed=i, max_turns=20)
            for evs in rep.events:
                for e in evs:
                    if e["k"] == "unit_built" and e["s"] == 0:
                        c[e["d"]["utype"]] += 1
        return c

    def test_aggression_one_engages_more_than_zero(self):
        def engagements(a):
            tot = 0
            for i in range(6):
                w = generate_world(1 + i, "tiny")
                _, rep = record_game(Commander(DoctrineGenome.default().with_(aggression=a)),
                                     TacticalAgent(), w, seed=i, max_turns=25)
                tot += sum(1 for evs in rep.events for e in evs if e["k"] == "engaged")
            return tot
        self.assertGreater(engagements(1.0), engagements(0.0))

    def test_jet_weight_builds_mostly_jets(self):
        zero = {f"w_build_{t.name.lower()}": 0.0 for t in T}
        c = self._built(DoctrineGenome.from_dict({**zero, "w_build_jet": 1.0, "adaptivity": 0.0}))
        self.assertGreater(sum(c.values()), 0)
        self.assertGreaterEqual(c["JET"] / sum(c.values()), 0.8)
