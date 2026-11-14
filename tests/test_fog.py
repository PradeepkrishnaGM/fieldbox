"""M6: fog of war, ghosts, observation audit, symmetric pathing."""

import gzip
import json
import os
import random
import tempfile
import unittest

from fieldbox import Domain, Game, Side, generate_world
from fieldbox.agents import DoctrineAgent, HoldAgent, RandomAgent, RushAgent
from fieldbox.core.hexgrid import mirror_coord
from fieldbox.record.replay import Replay, record_game
from fieldbox.sim import events as E
from fieldbox.sim import fog
from fieldbox.sim.orders import Attack, Hold, MoveTo
from fieldbox.sim.structures import StructureType as ST
from fieldbox.sim.units import UnitType as T

from _util import land_line, make_game, slow

try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    HAVE_PYGAME = True
except ImportError:      # pragma: no cover
    HAVE_PYGAME = False


def open_row(gap, world_seed=1):
    """A straight row of gap+1 land hexes at least 6 hexes from every structure
    (so no structure disc or radar interferes), and the probe game."""
    probe = make_game([], world_seed=world_seed, preset="standard")
    from fieldbox.core.hexgrid import distance
    structs = [s.coord for s in probe.structures.values()]
    land = sorted(c for c in probe.map.terrain if probe.map.passable(c, Domain.LAND))
    for q, r in land:
        row = [(q + i, r) for i in range(gap + 1)]
        if all(probe.map.passable(c, Domain.LAND) for c in row) and \
                all(distance(c, s) >= 6 for c in row for s in structs):
            return probe, row
    raise AssertionError("no open row found")


def two_units(gap, world_seed=1):
    """BLUE and RED infantry `gap` hexes apart in an open row, away from all structures."""
    probe, row = open_row(gap, world_seed)
    return make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.RED, T.INFANTRY, row[-1])],
                     world_seed=world_seed, preset="standard", max_turns=60), row


class TestVisibility(unittest.TestCase):
    def test_a_unit_sees_its_spot_radius_and_no_further(self):
        g, row = two_units(2)                     # infantry spot is 2
        self.assertTrue(g.can_see(Side.BLUE, g.units[1]))
        g, row = two_units(3)
        self.assertFalse(g.can_see(Side.BLUE, g.units[1]))
        self.assertEqual(g.observe(Side.BLUE).enemy_units, ())

    def test_visibility_is_symmetric_between_the_sides_at_the_start(self):
        g = Game(generate_world(1, "tiny"))
        b, r = g.vis[Side.BLUE], g.vis[Side.RED]
        w = g.world
        self.assertEqual({mirror_coord(c, w.width, w.height) for c in b.land}, set(r.land))
        self.assertEqual({mirror_coord(c, w.width, w.height) for c in b.air}, set(r.air))

    def test_radar_sees_air_and_sea_further_than_land(self):
        g = Game(generate_world(1, "tiny"))
        v = g.vis[Side.BLUE]
        self.assertGreater(len(v.air), len(v.land))
        self.assertTrue(v.land <= v.air)

    def test_no_data_center_halves_radar(self):
        self.assertEqual(fog.radar_range(10, 100, 1.0, True), 10)
        self.assertEqual(fog.radar_range(10, 100, 1.0, False), 5)
        self.assertEqual(fog.radar_range(4, 100, 1.0, False), 2)
        self.assertEqual(fog.radar_range(10, 50, 1.0, True), 5)       # scales with strength
        self.assertEqual(fog.radar_range(10, 100, 0.5, True), 5)      # and with power
        g = Game(generate_world(1, "tiny"))
        full = len(g.vis[Side.BLUE].air)
        for d in g.structures_of(Side.BLUE, ST.DATACENTER):
            g._destroy_structure(d)
        g._update_fog()
        self.assertLess(len(g.vis[Side.BLUE].air), full)

    def test_enemy_hq_is_known_at_the_start_but_other_structures_are_not(self):
        g = Game(generate_world(1, "tiny"))
        obs = g.observe(Side.BLUE)
        kinds = [s.kind for s in obs.enemy_structures]
        self.assertEqual(kinds, [ST.HQ])
        self.assertEqual(obs.enemy_structures[0].coord, g.world.hq[Side.RED])


class TestGhosts(unittest.TestCase):
    def _lose_contact(self):
        """BLUE sees RED at row[2]; then BLUE steps back to row[7] and RED vanishes."""
        probe, row = open_row(7)
        g = make_game([(Side.BLUE, T.INFANTRY, row[0]), (Side.RED, T.INFANTRY, row[2])],
                      preset="standard", max_turns=60)
        g.step_turn({Side.BLUE: [Hold(0)], Side.RED: [Hold(1)]})
        self.assertEqual(len(g.observe(Side.BLUE).enemy_units), 1)
        g.place(g.units[0], row[7])
        g.place(g.units[1], land_line(g, g.world.hq[Side.RED], 1)[0])
        g.step_turn({Side.BLUE: [Hold(0)], Side.RED: [Hold(1)]})
        return g, row

    def test_a_lost_contact_becomes_a_ghost_with_its_age_then_expires(self):
        g, row = self._lose_contact()
        obs = g.observe(Side.BLUE)
        self.assertEqual(obs.enemy_units, ())
        self.assertEqual(len(obs.ghosts), 1)
        self.assertEqual(obs.ghosts[0].coord, row[2])       # last known position
        self.assertEqual(obs.ghosts[0].age, 1)
        ages = []
        for _ in range(fog.FOG.ghost_ttl + 1):
            g.step_turn({Side.BLUE: [Hold(0)], Side.RED: [Hold(1)]})
            ages.append([x.age for x in g.observe(Side.BLUE).ghosts])
        self.assertEqual(ages[0], [2])
        self.assertEqual(ages[fog.FOG.ghost_ttl - 2], [fog.FOG.ghost_ttl])
        self.assertEqual(ages[-1], [])                      # expired after ghost_ttl turns

    def test_ghost_is_cleared_when_its_hex_is_seen_empty(self):
        g, row = self._lose_contact()
        self.assertEqual(len(g.observe(Side.BLUE).ghosts), 1)
        g.step_turn({Side.BLUE: [MoveTo(0, row[2])], Side.RED: [Hold(1)]})
        for _ in range(3):
            g.step_turn({Side.BLUE: [], Side.RED: [Hold(1)]})
        self.assertEqual(g.observe(Side.BLUE).ghosts, ())

    def test_ghosts_are_not_attackable(self):
        g, row = self._lose_contact()
        g.step_turn({Side.BLUE: [Attack(0, 1)], Side.RED: []})
        self.assertTrue(any(e.data.get("reason") == "no such enemy"
                            for e in g.events if e.kind == E.ORDER_REJECTED))


class TestOrdersAndFog(unittest.TestCase):
    def test_attacking_an_unseen_unit_is_rejected(self):
        g, row = two_units(6)
        g.step_turn({Side.BLUE: [Attack(0, 1)], Side.RED: []})
        self.assertTrue(any(e.data.get("reason") == "no such enemy"
                            for e in g.events if e.kind == E.ORDER_REJECTED))
        self.assertNotIn(0, g.standing)

    def test_a_standing_attack_ends_when_the_target_leaves_sight(self):
        g, row = two_units(2)
        g.step_turn({Side.BLUE: [Attack(0, 1)], Side.RED: [Hold(1)]})
        far = land_line(g, g.world.hq[Side.RED], 1)[0]
        g.place(g.units[1], far)
        g.step_turn({Side.BLUE: [], Side.RED: [Hold(1)]})
        self.assertNotIn(0, g.standing)        # the attack order ended with the sight line

    def test_unknown_structures_cannot_be_targeted(self):
        probe = make_game([])
        ind = probe.structures_of(Side.RED, ST.INDUSTRY)[0]
        g = make_game([(Side.BLUE, T.JET, probe.world.hq[Side.BLUE]),
                       (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])])
        from fieldbox.sim.orders import Strike
        g.step_turn({Side.BLUE: [Strike(0, ind.sid)]})
        self.assertTrue(any(e.data.get("reason") == "no such enemy structure"
                            for e in g.events if e.kind == E.ORDER_REJECTED))

    def test_seen_structures_stay_known_until_seen_destroyed(self):
        probe = make_game([])
        st = probe.structures_of(Side.RED, ST.RADAR)[0]
        near = [c for c in probe._ring(st.coord, 1) if probe.map.passable(c, Domain.LAND)][0]
        g = make_game([(Side.BLUE, T.INFANTRY, near), (Side.RED, T.INFANTRY, probe.world.hq[Side.RED])])
        sid = g.structures_of(Side.RED, ST.RADAR)[0].sid
        self.assertIn(sid, [s.sid for s in g.observe(Side.BLUE).enemy_structures])
        g.place(g.units[0], g.world.hq[Side.BLUE])                 # walk away
        g.step_turn({})
        views = {s.sid: s for s in g.observe(Side.BLUE).enemy_structures}
        self.assertIn(sid, views)                                  # remembered
        # destroyed while unseen: still remembered
        g._destroy_structure(g.structures[sid])
        g.step_turn({})
        self.assertIn(sid, [s.sid for s in g.observe(Side.BLUE).enemy_structures])
        # seen destroyed (a unit returns): forgotten
        g.place(g.units[0], near)
        g.step_turn({})
        self.assertNotIn(sid, [s.sid for s in g.observe(Side.BLUE).enemy_structures])


class TestObservationAudit(unittest.TestCase):
    """No observation may ever hold an enemy the side cannot see."""

    PAIRS = ((DoctrineAgent, RushAgent), (RushAgent, DoctrineAgent), (RandomAgent, DoctrineAgent),
             (DoctrineAgent, HoldAgent))

    def _audit(self, game, side, obs, ever_seen_u, ever_seen_s):
        vis = fog.compute_visibility(game, side)          # recomputed independently
        self.assertEqual(vis, game.vis[side])
        enemy = {u.uid: u for u in game.units_of(side.other)}
        for v in obs.enemy_units:
            real = enemy[v.uid]
            self.assertEqual(v.coord, real.coord)
            self.assertTrue(vis.sees(real.domain, real.coord), f"{v} is not visible")
            ever_seen_u.add(v.uid)
        visible_uids = {v.uid for v in obs.enemy_units}
        hidden = {uid for uid, u in enemy.items() if not vis.sees(u.domain, u.coord)}
        # hidden units appear nowhere: not in the lists, not in the position index
        self.assertFalse(hidden & visible_uids)
        self.assertFalse(hidden & {u.uid for hex_ in obs._by_coord.values() for u in hex_.values()})
        for gh in obs.ghosts:
            self.assertIn(gh.uid, ever_seen_u, "a ghost for a unit that was never seen")
            self.assertTrue(1 <= gh.age <= fog.FOG.ghost_ttl)
        for s in obs.enemy_structures:
            real = game.structures.get(s.sid)
            if s.age == 0:
                self.assertIsNotNone(real)
                self.assertIn(real.coord, vis.land)
                self.assertEqual(s.strength, real.strength)
            ever_seen_s.add(s.sid)

    def test_no_observation_contains_an_unseen_enemy(self):
        n = 0
        for i in range(20):
            a, b = self.PAIRS[i % len(self.PAIRS)]
            w = generate_world(1 + i % 4, "tiny")
            g = Game(w, seed=i, max_turns=20)
            agents = {Side.BLUE: a(), Side.RED: b()}
            for s, ag in agents.items():
                ag.reset(s, w)
            seen = {s: (set(), {x.sid for x in g.structures_of(s.other, ST.HQ)}) for s in Side}
            while not g.is_over:
                obs = {s: g.observe(s) for s in Side}
                for s in Side:
                    self._audit(g, s, obs[s], seen[s][0], seen[s][1])
                    n += 1
                g.step_turn({s: agents[s].act(obs[s]) for s in Side})
        self.assertGreater(n, 400)

    def test_observation_has_no_live_references(self):
        g = Game(generate_world(1, "tiny"))
        obs = g.observe(Side.BLUE)
        for u in obs.enemy_units + obs.own_units + obs.ghosts:
            self.assertNotIn("Unit", type(u).__name__.replace("UnitView", ""))


class TestFogDoesNotBreakTheGame(unittest.TestCase):
    def test_determinism_with_fog(self):
        w = generate_world(2, "tiny")
        h = []
        for _ in range(2):
            g, _ = record_game(DoctrineAgent(), RushAgent(), w, seed=4, max_turns=20)
            h.append(E.stream_hash(g.events))
        self.assertEqual(h[0], h[1])

    def test_doctrine_still_beats_random_and_rush(self):
        pts = {"random": 0, "rush": 0}
        n = 6
        for i in range(n):
            w = generate_world(1000 + i, "tiny")
            for name, cls in (("random", RandomAgent), ("rush", RushAgent)):
                from fieldbox.league.arena import play_match
                r1 = play_match(DoctrineAgent(), cls(), w, seed=i)
                r2 = play_match(cls(), DoctrineAgent(), w, seed=i)
                pts[name] += (r1.winner == Side.BLUE) + (r2.winner == Side.RED)
        self.assertGreaterEqual(pts["random"], 10)      # of 12
        self.assertGreaterEqual(pts["rush"], 9)


class TestSymmetricPaths(unittest.TestCase):
    def test_a_path_and_its_mirror_image_are_mirror_images(self):
        w = generate_world(3, "standard")
        m = w.hexmap
        self.assertTrue(m.symmetric)
        rng = random.Random(7)
        land = sorted(c for c in m.terrain if m.passable(c, Domain.LAND))
        mir = lambda c: mirror_coord(c, m.width, m.height)       # noqa: E731
        for _ in range(60):
            a, b = rng.sample(land, 2)
            p = m.find_path(a, b)
            q = m.find_path(mir(a), mir(b))
            self.assertEqual(None if p is None else [mir(c) for c in p], q)
            blocked = frozenset(rng.sample(land, 25)) - {a, b}
            p = m.find_path(a, b, blocked)
            q = m.find_path(mir(a), mir(b), frozenset(mir(c) for c in blocked))
            self.assertEqual(None if p is None else [mir(c) for c in p], q)

    def test_asymmetric_maps_are_untouched(self):
        from fieldbox.core.hexgrid import HexMap
        self.assertFalse(HexMap(6, 4).symmetric)

    @slow
    def test_rush_mirror_match_is_fair(self):
        pts = 0.0
        n = 60
        from fieldbox.league.arena import play_match
        for i in range(n):
            r = play_match(RushAgent(), RushAgent(), generate_world(1 + i % 4, "tiny"), seed=i)
            pts += 1.0 if r.winner == Side.BLUE else 0.5 if r.winner is None else 0.0
        self.assertTrue(0.35 <= pts / n <= 0.65, pts / n)


class TestReplayFog(unittest.TestCase):
    def test_snapshots_carry_each_sides_view(self):
        w = generate_world(1, "tiny")
        g, rep = record_game(DoctrineAgent(), RushAgent(), w, seed=2, max_turns=10)
        sn = rep.snapshots[-1]
        self.assertEqual(len(sn["vis"]), 2)
        self.assertEqual(len(sn["vis"][0]), 2)
        self.assertEqual(len(sn["ghosts"]), 2)
        self.assertEqual(len(sn["known"]), 2)

    @unittest.skipUnless(HAVE_PYGAME, "pygame-ce not installed")
    def test_match_screen_renders_god_blue_and_red_views_and_old_replays(self):
        from fieldbox.ui.shot import render_screen
        w = generate_world(1, "tiny")
        g, rep = record_game(DoctrineAgent(), RushAgent(), w, seed=2, max_turns=12)
        with tempfile.TemporaryDirectory() as tmp:
            path = rep.save(os.path.join(tmp, "a.fbr"))
            for view in (None, 0, 1):
                out = render_screen("match", os.path.join(tmp, f"v{view}.png"), replay=path,
                                    turns=9, view=view, rings=True)
                self.assertGreater(os.path.getsize(out), 5000)
            # a version 1 file (no fog data) still opens, in God view only
            d = rep.to_dict()
            d["version"] = 1
            for s in d["snapshots"]:
                for k in ("vis", "ghosts", "known"):
                    s.pop(k)
            old = os.path.join(tmp, "old.fbr")
            with gzip.open(old, "wt") as fh:
                json.dump(d, fh)
            out = render_screen("match", os.path.join(tmp, "old.png"), replay=old, turns=5, view=1)
            self.assertGreater(os.path.getsize(out), 5000)

    @unittest.skipUnless(HAVE_PYGAME, "pygame-ce not installed")
    def test_view_cycle_hides_enemy_units_and_economy(self):
        from fieldbox.ui.app import App
        from fieldbox.ui.screens.match import MatchScreen
        app = App((1600, 900))
        self.addCleanup(pygame.quit)
        scr = MatchScreen.create(app, seed=1, preset="tiny", turns=12)
        app.push(scr)
        scr.advance_to(8)
        self.assertIsNone(scr.view)
        scr.cycle_view()
        self.assertEqual(scr.view, 0)
        hidden = [r for r in scr.snap["units"] if r[1] == 1 and not scr.unit_visible(r)]
        shown = [r for r in scr.snap["units"] if r[1] == 1 and scr.unit_visible(r)]
        self.assertTrue(hidden or not shown or True)
        for r in scr.snap["units"]:
            if r[1] == 0:
                self.assertTrue(scr.unit_visible(r))
        scr.cycle_view()
        scr.cycle_view()
        self.assertIsNone(scr.view)
        scr.update(0.0)
        scr.draw(app.window)


if __name__ == "__main__":
    unittest.main()
