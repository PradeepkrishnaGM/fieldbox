"""Core invariants. Determinism is the one that matters most -- without it
every later evaluation number is noise."""

import re
import unittest

from fieldbox import Domain, Game, Scenario, Side, Terrain, distance
from fieldbox.agents import DoctrineAgent, RandomAgent, RushAgent
from fieldbox.league.arena import play_match
from fieldbox.core.hexgrid import HexMap


class TestHexGrid(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(distance((0, 0), (0, 0)), 0)
        self.assertEqual(distance((0, 0), (1, 0)), 1)
        self.assertEqual(distance((0, 0), (2, -1)), 2)
        self.assertEqual(distance((0, 0), (-3, 1)), 3)

    def test_path_avoids_water(self):
        m = HexMap(8, 6)
        for r in range(6):
            coord = (3 - r // 2, r)
            if r != 5 and coord in m.terrain:
                m.set_terrain(coord, Terrain.WATER)
        path = m.find_path((0, 0), (5 - 2, 4))
        self.assertIsNotNone(path)
        for step in path:
            self.assertNotEqual(m.get(step), Terrain.WATER)

    def test_path_none_when_unreachable(self):
        m = HexMap(5, 5)
        for coord in list(m.terrain):
            if coord != (0, 0):
                m.set_terrain(coord, Terrain.WATER)
        self.assertIsNone(m.find_path((0, 0), (2, 2)))


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_outcome(self):
        scenario = Scenario(map_seed=7)
        results = []
        for _ in range(3):
            r = play_match(DoctrineAgent(), RushAgent(), scenario, seed=42)
            results.append((r.winner, r.vp[Side.BLUE], r.vp[Side.RED], r.turns))
        self.assertEqual(len(set(results)), 1, "same seed must reproduce exactly")

    def test_different_seed_differs(self):
        scenario = Scenario(map_seed=7)
        a = play_match(RandomAgent(1), RandomAgent(2), scenario, seed=1)
        b = play_match(RandomAgent(1), RandomAgent(2), scenario, seed=999)
        self.assertNotEqual(
            (a.vp[Side.BLUE], a.vp[Side.RED]), (b.vp[Side.BLUE], b.vp[Side.RED])
        )

    def test_scenario_seed_reproducible(self):
        a, b = Scenario(map_seed=11), Scenario(map_seed=11)
        self.assertEqual(a._terrain, b._terrain)
        self.assertEqual(a.objectives, b.objectives)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario.preset("combined_arms", map_seed=3)
        self.game = Game(self.scenario, seed=0)

    def test_units_placed(self):
        expected = sum(n for _, n in self.scenario.oob)
        self.assertEqual(len(self.game.units_of(Side.BLUE)), expected)
        self.assertEqual(len(self.game.units_of(Side.RED)), expected)

    def test_no_stacking_within_a_domain(self):
        """A hex holds at most one unit per domain -- a helicopter may fly
        over a tank, but two tanks cannot share a hex."""
        seen = set()
        for u in self.game.units.values():
            key = (u.coord, u.domain)
            self.assertNotIn(key, seen, f"{u} stacked illegally")
            seen.add(key)

    def test_legal_orders_nonempty(self):
        for u in self.game.units_of(Side.BLUE):
            self.assertGreater(len(self.game.legal_orders(u)), 0)

    def test_combat_causes_losses(self):
        blue = self.game.units_of(Side.BLUE)[0]
        red = self.game.units_of(Side.RED)[0]
        self.game.place(red, (blue.q + 1, blue.r))
        b0, r0 = blue.strength, red.strength
        self.game.resolve_attack(blue, red)
        self.assertLess(red.strength, r0)
        self.assertLess(blue.strength, b0)

    def test_game_terminates(self):
        result = play_match(RandomAgent(1), RandomAgent(2), self.scenario, seed=5)
        self.assertLessEqual(result.turns, self.scenario.max_turns)

    def test_no_units_off_map(self):
        play = Game(self.scenario, seed=2)
        agents = {Side.BLUE: RushAgent(), Side.RED: DoctrineAgent()}
        while not play.is_over:
            side = play.current_side
            play.step(agents[side].act(play, side))
            for u in play.units.values():
                self.assertTrue(play.map.in_bounds(u.coord))
                self.assertTrue(play.map.passable(u.coord))

    def test_sidc_wellformed(self):
        """Field positions are easy to get wrong and milsymbol renders the
        wrong echelon marker silently if they are."""
        from fieldbox.sim.units import DIMENSION_CODE
        for u in self.game.units.values():
            code = u.sidc()
            self.assertEqual(len(code), 15)
            self.assertEqual(code[0], "S")                       # warfighting
            self.assertEqual(code[1], "F" if u.side is Side.BLUE else "H")
            self.assertEqual(code[2], DIMENSION_CODE[u.domain])  # battle dimension
            self.assertEqual(code[3], "P")                       # present
            self.assertEqual(code[4:10], u.stats.sidc_function)  # function ID
            if u.domain is Domain.LAND:
                self.assertEqual(code[11], "E")                  # echelon: company

    def test_sidc_examples(self):
        from fieldbox.sim.units import Unit, UnitType
        self.assertEqual(Unit(0, Side.BLUE, UnitType.INFANTRY, 0, 0).sidc(),
                         "SFGPUCI----E---")
        self.assertEqual(Unit(1, Side.RED, UnitType.TANK, 0, 0).sidc(),
                         "SHGPUCA----E---")
        # Air and naval tracks carry a different battle dimension and no
        # ground echelon.
        self.assertEqual(Unit(2, Side.BLUE, UnitType.JET, 0, 0).sidc()[2], "A")
        self.assertEqual(Unit(3, Side.RED, UnitType.SHIP, 0, 0).sidc()[2], "S")


class TestVictoryConditions(unittest.TestCase):
    """Side.BLUE == 0, so any `if winner:` check silently reports a BLUE win
    as a draw. Regression test for exactly that."""

    def test_blue_annihilation_win_reported(self):
        scenario = Scenario(map_seed=3)
        game = Game(scenario, seed=0)
        for u in game.units_of(Side.RED):
            game.units.pop(u.uid)
        self.assertTrue(game.is_over)
        self.assertIs(game.winner(), Side.BLUE)
        self.assertIsNotNone(game.winner())

    def test_red_annihilation_win_reported(self):
        scenario = Scenario(map_seed=3)
        game = Game(scenario, seed=0)
        for u in game.units_of(Side.BLUE):
            game.units.pop(u.uid)
        self.assertIs(game.winner(), Side.RED)

    def test_draw_on_equal_vp(self):
        scenario = Scenario(map_seed=3, max_turns=1)
        game = Game(scenario, seed=0)
        game.turn = 1
        game.vp[Side.BLUE] = game.vp[Side.RED] = 5.0
        self.assertIsNone(game.winner())

    def test_json_export_holder_not_swallowed(self):
        import json
        from fieldbox.record.render import to_json
        scenario = Scenario(map_seed=3)
        game = Game(scenario, seed=0)
        obj = next(iter(game.objectives))
        blue = game.units_of(Side.BLUE)[0]
        game.place(blue, obj)
        payload = json.loads(to_json(game))
        holders = [o["holder"] for o in payload["objectives"] if (o["q"], o["r"]) == obj]
        self.assertEqual(holders, ["BLUE"])


class TestPositionIndex(unittest.TestCase):
    """The coord->unit index is a denormalised cache. If it ever drifts from
    the units dict, movement and combat silently corrupt."""

    def _assert_consistent(self, game):
        alive = {}
        for u in game.units.values():
            if u.alive:
                alive.setdefault(u.coord, {})[u.domain] = u
        self.assertEqual(
            game._by_coord, alive,
            f"index drift at turn {game.turn}",
        )

    def test_index_consistent_through_full_game(self):
        scenario = Scenario(map_seed=6)
        game = Game(scenario, seed=11)
        agents = {Side.BLUE: DoctrineAgent(), Side.RED: RushAgent()}
        self._assert_consistent(game)
        while not game.is_over:
            side = game.current_side
            game.step(agents[side].act(game, side))
            self._assert_consistent(game)

    def test_index_consistent_under_random_play(self):
        scenario = Scenario(map_seed=9)
        for seed in range(4):
            game = Game(scenario, seed=seed)
            agents = {Side.BLUE: RandomAgent(seed), Side.RED: RandomAgent(seed + 50)}
            while not game.is_over:
                side = game.current_side
                game.step(agents[side].act(game, side))
                self._assert_consistent(game)


class TestAgentStrength(unittest.TestCase):
    def test_doctrine_beats_random(self):
        scenario = Scenario(map_seed=4)
        wins = 0
        for i in range(12):
            r = play_match(DoctrineAgent(), RandomAgent(i), scenario, seed=i)
            if r.winner is Side.BLUE:
                wins += 1
        self.assertGreaterEqual(wins, 9, f"doctrine only won {wins}/12 vs random")


class TestRendering(unittest.TestCase):
    """Regressions from the first playtest: ARM and ART both rendered 'A',
    and full-strength cells were 3 chars while weak ones were 2, so the hex
    rows drifted out of alignment."""

    ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

    def test_glyphs_unique(self):
        from fieldbox.sim.units import STATS, UnitType
        glyphs = [STATS[t].glyph for t in UnitType]
        self.assertEqual(len(set(glyphs)), len(glyphs), f"glyph collision: {glyphs}")

    def test_artillery_distinct_from_armor(self):
        from fieldbox.sim.units import STATS, UnitType
        self.assertNotEqual(
            STATS[UnitType.TANK].glyph, STATS[UnitType.ARTILLERY].glyph
        )

    def test_map_rows_aligned(self):
        from fieldbox.record.render import render
        scenario = Scenario(map_seed=2)
        game = Game(scenario, seed=1)
        agents = {Side.BLUE: DoctrineAgent(), Side.RED: RushAgent()}
        for _ in range(12):
            if game.is_over:
                break
            side = game.current_side
            game.step(agents[side].act(game, side))
        body = render(game, color=False).split("\n")[2:2 + game.map.height]
        # No rstrip: trailing space is part of the fixed-width cell, and
        # stripping it makes width depend on whether the last cell is terrain.
        widths = {len(self.ANSI_RE.sub("", row)) for row in body}
        self.assertLessEqual(
            len(widths), 2, f"hex rows ragged, widths={sorted(widths)}"
        )

    def test_living_unit_never_shows_zero(self):
        from fieldbox.record.render import _strength_pips
        for strength in (0.5, 1.0, 4.0, 9.9):
            self.assertNotEqual(
                _strength_pips(strength).strip(), "0",
                f"strength {strength} rendered as dead",
            )
        self.assertEqual(_strength_pips(100.0).strip(), "10")


class TestArtilleryDoctrine(unittest.TestCase):
    """Artillery was walking onto the objective and dying in 30/30 games,
    because the standoff was computed as reach - 2 = 1 hex."""

    def _run(self, n=20):
        from fieldbox.sim.units import UnitType
        # The 'legacy' preset has no aircraft. This test is about whether the
        # guns position themselves sanely, so air power -- which kills
        # artillery regardless of positioning -- would only add noise.
        scenario = Scenario.preset("legacy", map_seed=1)
        deaths = 0
        for seed in range(n):
            game = Game(scenario, seed=seed)
            agents = {Side.BLUE: DoctrineAgent(), Side.RED: RushAgent()}
            guns = [u.uid for u in game.units_of(Side.BLUE)
                    if u.utype is UnitType.ARTILLERY]
            while not game.is_over:
                side = game.current_side
                game.step(agents[side].act(game, side))
            if not any(u.uid in guns for u in game.units_of(Side.BLUE)):
                deaths += 1
        return deaths, n

    def test_artillery_survives_sometimes(self):
        deaths, n = self._run()
        self.assertLess(
            deaths, n,
            "artillery died in every game -- standoff logic is broken again",
        )

    def test_firing_position_respects_standoff(self):
        """Standoff applies to *ground* threats only. You cannot outrange a
        jet that moves 16, so air is handled by friendly cover instead."""
        from fieldbox.agents.doctrine import MIN_STANDOFF, Plan
        from fieldbox.sim.units import UnitType
        scenario = Scenario.preset("legacy", map_seed=1)
        game = Game(scenario, seed=0)
        agent = DoctrineAgent()
        gun = next(u for u in game.units_of(Side.BLUE)
                   if u.utype is UnitType.ARTILLERY)
        enemies = game.units_of(Side.RED)
        plan = Plan(main_effort=None, enemy_air_cover=set(), own_air_cover=set())
        spot = agent._firing_position(game, Side.BLUE, gun, enemies, plan)
        if spot is not None:
            ground = [e for e in enemies if e.domain is not Domain.AIR]
            nearest = min(distance(spot, e.coord) for e in ground)
            self.assertGreaterEqual(nearest, MIN_STANDOFF)

    def test_guns_survive_a_mirror_match(self):
        """With both sides fielding air defence, artillery should live."""
        from fieldbox.sim.units import UnitType
        scenario = Scenario.preset("combined_arms", map_seed=1)
        deaths = 0
        for seed in range(10):
            game = Game(scenario, seed=seed)
            agents = {Side.BLUE: DoctrineAgent(), Side.RED: DoctrineAgent()}
            guns = [u.uid for u in game.units_of(Side.BLUE)
                    if u.utype is UnitType.ARTILLERY]
            while not game.is_over:
                side = game.current_side
                game.step(agents[side].act(game, side))
            if not any(u.uid in guns for u in game.units_of(Side.BLUE)):
                deaths += 1
        self.assertLessEqual(deaths, 3, f"guns died in {deaths}/10 mirror matches")


class TestReconDoctrine(unittest.TestCase):
    """Recon died in 20/20 games: it was ordered straight at the main effort,
    which is where the enemy is, with a 0.60 defence coefficient."""

    def _deaths(self, n=15):
        from fieldbox.sim.units import UnitType
        # Air-free roster: this asserts the screening logic, not survivability
        # against helicopters.
        scenario = Scenario.preset("legacy", map_seed=1)
        deaths = 0
        for seed in range(n):
            game = Game(scenario, seed=seed)
            agents = {Side.BLUE: DoctrineAgent(), Side.RED: RushAgent()}
            scouts = [u.uid for u in game.units_of(Side.BLUE)
                      if u.utype is UnitType.RECON]
            while not game.is_over:
                side = game.current_side
                game.step(agents[side].act(game, side))
            if not any(u.uid in scouts for u in game.units_of(Side.BLUE)):
                deaths += 1
        return deaths, n

    def test_recon_usually_survives(self):
        deaths, n = self._deaths()
        self.assertLessEqual(
            deaths, n // 3,
            f"recon died in {deaths}/{n} games -- screening logic regressed",
        )

    def test_screen_standoff_never_below_floor(self):
        """A standoff above the observation radius makes every hex invalid,
        so the search returns None and the fallback used to charge."""
        from fieldbox.agents.doctrine import RECON_MIN_STANDOFF
        from fieldbox.sim.units import STATS, UnitType
        standoff = max(RECON_MIN_STANDOFF, STATS[UnitType.RECON].spot)
        self.assertGreaterEqual(standoff, RECON_MIN_STANDOFF)

    def test_screen_position_respects_standoff(self):
        from fieldbox.agents.doctrine import RECON_MIN_STANDOFF
        from fieldbox.sim.units import UnitType
        scenario = Scenario.preset("legacy", map_seed=1)
        game = Game(scenario, seed=0)
        agents = {Side.BLUE: DoctrineAgent(), Side.RED: RushAgent()}
        for _ in range(6):
            if game.is_over:
                break
            side = game.current_side
            game.step(agents[side].act(game, side))
        scouts = [u for u in game.units_of(Side.BLUE)
                  if u.utype is UnitType.RECON]
        enemies = game.units_of(Side.RED)
        if scouts and enemies:
            post = DoctrineAgent()._screen_position(
                game, Side.BLUE, scouts[0], enemies
            )
            if post is not None:
                ground = [e for e in enemies if e.domain is not Domain.AIR]
                nearest = min(distance(post, e.coord) for e in ground)
                self.assertGreaterEqual(nearest, RECON_MIN_STANDOFF)


class TestHoldPosture(unittest.TestCase):
    """A dug-in defender was losing to random play because it attacked out of
    its own entrenchment, resetting the bonus it exists to accumulate."""

    def test_holds_against_healthy_adjacent_enemy(self):
        from fieldbox.agents import HoldAgent
        from fieldbox.sim.engine import Attack, Hold
        scenario = Scenario.preset("legacy", map_seed=1)
        game = Game(scenario, seed=0)
        objective = max(game.objectives, key=lambda c: game.objectives[c])
        defender = game.units_of(Side.BLUE)[0]
        attacker = game.units_of(Side.RED)[0]
        game.place(defender, objective)
        neighbour = next(
            n for n in __import__("fieldbox.core.hexgrid", fromlist=["neighbors"])
            .neighbors(*objective)
            if game.map.passable(n) and game.unit_at(n) is None
        )
        game.place(attacker, neighbour)
        attacker.strength = 100.0
        order = HoldAgent().act(game, Side.BLUE)[defender.uid]
        self.assertIsInstance(
            order, Hold,
            "defender attacked a full-strength enemy out of its entrenchment",
        )

    def test_counterattacks_broken_enemy(self):
        from fieldbox.agents import HoldAgent
        from fieldbox.agents.scripted import COUNTERATTACK_THRESHOLD
        from fieldbox.sim.engine import Attack
        from fieldbox.core.hexgrid import neighbors
        scenario = Scenario.preset("legacy", map_seed=1)
        game = Game(scenario, seed=0)
        objective = max(game.objectives, key=lambda c: game.objectives[c])
        defender = game.units_of(Side.BLUE)[0]
        attacker = game.units_of(Side.RED)[0]
        game.place(defender, objective)
        neighbour = next(
            n for n in neighbors(*objective)
            if game.map.passable(n) and game.unit_at(n) is None
        )
        game.place(attacker, neighbour)
        attacker.strength = COUNTERATTACK_THRESHOLD - 5.0
        order = HoldAgent().act(game, Side.BLUE)[defender.uid]
        self.assertIsInstance(order, Attack)

    def test_hold_beats_random(self):
        from fieldbox.agents import HoldAgent
        scenario = Scenario.preset("legacy", map_seed=1)
        wins = 0
        for seed in range(15):
            result = play_match(HoldAgent(), RandomAgent(seed), scenario, seed=seed)
            if result.winner is Side.BLUE:
                wins += 1
        self.assertGreaterEqual(
            wins, 11, f"entrenched defender only beat random {wins}/15 times"
        )

    def test_no_unit_left_idle_at_deployment(self):
        from fieldbox.agents import HoldAgent
        scenario = Scenario.preset("legacy", map_seed=1)
        game = Game(scenario, seed=0)
        start = {u.uid: u.coord for u in game.units_of(Side.BLUE)}
        agents = {Side.BLUE: HoldAgent(), Side.RED: RandomAgent(0)}
        moved = set()
        while not game.is_over:
            side = game.current_side
            game.step(agents[side].act(game, side))
            for u in game.units_of(Side.BLUE):
                if u.coord != start.get(u.uid):
                    moved.add(u.uid)
        idle = set(start) - moved
        self.assertLessEqual(
            len(idle), 1,
            f"{len(idle)}/{len(start)} units never left the deployment line",
        )


class TestLadderSanity(unittest.TestCase):
    def test_random_is_the_weakest_agent(self):
        """If random is not last, a baseline is broken -- which is exactly
        how the hold-agent posture bug surfaced."""
        from fieldbox.agents import HoldAgent
        scenario = Scenario.preset("legacy", map_seed=1)
        for name, agent_cls in (("doctrine", DoctrineAgent), ("hold", HoldAgent)):
            wins = 0
            for seed in range(10):
                r = play_match(agent_cls(), RandomAgent(seed), scenario, seed=seed)
                if r.winner is Side.BLUE:
                    wins += 1
            self.assertGreaterEqual(
                wins, 7, f"{name} only beat random {wins}/10"
            )


class TestDomains(unittest.TestCase):
    """Domain rules are what make air and naval units more than reskinned
    infantry. Each of these would silently degrade into 'fast ground unit'
    if it regressed."""

    def setUp(self):
        self.scenario = Scenario.preset("littoral", map_seed=1)
        self.game = Game(self.scenario, seed=0)

    def _first(self, domain):
        return next(
            (u for u in self.game.units.values() if u.domain is domain), None
        )

    def test_air_may_stack_over_ground(self):
        air = self._first(Domain.AIR)
        land = self._first(Domain.LAND)
        self.assertIsNotNone(air)
        self.game.place(air, land.coord)
        occupants = {u.domain for u in self.game.units_at(land.coord)}
        self.assertIn(Domain.AIR, occupants)
        self.assertIn(Domain.LAND, occupants)

    def test_ground_may_not_stack_with_ground(self):
        seen = set()
        for u in self.game.units.values():
            key = (u.coord, u.domain)
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_ships_confined_to_water(self):
        from fieldbox.agents import DoctrineAgent as DA
        game = Game(self.scenario, seed=2)
        agents = {Side.BLUE: DA(), Side.RED: DA()}
        while not game.is_over:
            side = game.current_side
            game.step(agents[side].act(game, side))
            for u in game.units.values():
                if u.domain is Domain.SEA:
                    self.assertEqual(
                        game.map.get(u.coord), Terrain.WATER,
                        f"{u} left the water",
                    )

    def test_land_cannot_enter_water(self):
        land = self._first(Domain.LAND)
        water = next(
            c for c, t in self.game.map.terrain.items() if t is Terrain.WATER
        )
        self.assertFalse(self.game.map.passable(water, Domain.LAND))
        self.assertTrue(self.game.map.passable(water, Domain.SEA))
        self.assertTrue(self.game.map.passable(water, Domain.AIR))

    def test_only_capturing_units_hold_objectives(self):
        from fieldbox.sim.units import STATS
        objective = next(iter(self.game.objectives))
        jet = next(
            (u for u in self.game.units.values()
             if not u.stats.can_capture and u.domain is Domain.AIR), None
        )
        if jet is None:
            self.skipTest("no air unit in this preset")
        for u in list(self.game.units_at(objective)):
            self.game._kill(u)
        self.game.place(jet, objective)
        self.assertIsNone(
            self.game.objective_holder(objective),
            "an aircraft overhead should not capture the ground",
        )

    def test_engagement_matrix_enforced(self):
        from fieldbox.sim.units import STATS, UnitType
        # Air defence engages aircraft and nothing else.
        self.assertEqual(STATS[UnitType.SAM].targets, frozenset({Domain.AIR}))
        # Ground manoeuvre cannot shoot at aircraft, which is what makes air
        # defence necessary rather than decorative.
        self.assertNotIn(Domain.AIR, STATS[UnitType.TANK].targets)
        self.assertNotIn(Domain.AIR, STATS[UnitType.INFANTRY].targets)

    def test_illegal_attack_is_a_no_op(self):
        from fieldbox.sim.engine import Attack
        from fieldbox.sim.units import UnitType
        game = Game(self.scenario, seed=0)
        sam = next(
            (u for u in game.units_of(Side.BLUE) if u.utype is UnitType.SAM), None
        )
        ground = next(
            u for u in game.units_of(Side.RED) if u.domain is Domain.LAND
        )
        if sam is None:
            self.skipTest("no SAM in this preset")
        before = ground.strength
        game._execute(sam, Attack(sam.uid, ground.uid))
        self.assertEqual(
            ground.strength, before,
            "air defence engaged a ground unit it cannot target",
        )

    def test_ground_zoc_does_not_stop_aircraft(self):
        """Zone of control is per domain. Infantry halting a jet would make
        air power pointless."""
        from fieldbox.core.hexgrid import neighbors
        game = Game(self.scenario, seed=0)
        air = next(u for u in game.units.values()
                   if u.domain is Domain.AIR and u.side is Side.BLUE)
        enemy_ground = next(
            u for u in game.units_of(Side.RED) if u.domain is Domain.LAND
        )
        adjacent = next(
            n for n in neighbors(*enemy_ground.coord)
            if game.map.passable(n, Domain.AIR)
        )
        game.place(air, adjacent)
        self.assertEqual(
            game.enemies_adjacent(air.coord, air.side, Domain.AIR), [],
            "ground units must not exert zone of control over aircraft",
        )


class TestRoster(unittest.TestCase):
    def test_glyphs_unique_across_all_types(self):
        from fieldbox.sim.units import STATS, UnitType
        glyphs = [STATS[t].glyph for t in UnitType]
        self.assertEqual(len(set(glyphs)), len(glyphs), f"collision: {glyphs}")

    def test_every_unit_has_a_valid_sidc(self):
        from fieldbox.sim.units import DIMENSION_CODE, STATS, Unit, UnitType
        for t in UnitType:
            u = Unit(0, Side.BLUE, t, 0, 0)
            code = u.sidc()
            self.assertEqual(len(code), 15, f"{t.name}: {code}")
            self.assertEqual(code[2], DIMENSION_CODE[STATS[t].domain])

    def test_air_defence_is_effective_against_air(self):
        """A SAM that cannot kill an aircraft faster than the aircraft kills
        its protectee is decorative. This caught a 6.7-turn vs 2.2-attack
        mismatch that made air defence useless."""
        from fieldbox.sim.engine import BASE_LOSS
        from fieldbox.sim.units import NO_RETURN_FIRE_MULT, STATS, UnitType
        sam = STATS[UnitType.SAM]
        helo = STATS[UnitType.HELICOPTER]
        art = STATS[UnitType.ARTILLERY]
        sam_odds = sam.attack * sam.fire_coef / helo.defense
        turns_to_kill_helo = 100 / min(45, BASE_LOSS * sam_odds ** 0.7)
        strike_odds = helo.attack / (art.defense * NO_RETURN_FIRE_MULT)
        attacks_to_kill_gun = 100 / min(45, BASE_LOSS * strike_odds ** 0.7)
        self.assertLess(
            turns_to_kill_helo, attacks_to_kill_gun * 2.0,
            "air defence is too slow to ever protect anything",
        )

    def test_presets_place_every_unit(self):
        from fieldbox.sim.scenario import PRESETS
        for name in PRESETS:
            scenario = Scenario.preset(name, map_seed=1)
            game = Game(scenario, seed=0)
            expected = sum(n for _, n in scenario.oob)
            for side in (Side.BLUE, Side.RED):
                self.assertEqual(
                    len(game.units_of(side)), expected,
                    f"preset {name}: {side.name} lost units at deployment",
                )

    def test_naval_preset_implies_a_coastal_map(self):
        """A ship on the inland template has nowhere to float."""
        scenario = Scenario.preset("littoral", map_seed=1)
        self.assertEqual(scenario.template, "coastal")
        game = Game(scenario, seed=0)
        for u in game.units.values():
            if u.domain is Domain.SEA:
                self.assertEqual(game.map.get(u.coord), Terrain.WATER)


class TestMapFairness(unittest.TestCase):
    """A map where one side starts closer to the objectives invalidates every
    per-colour measurement taken on it. At width 18 the centre column sat one
    hex nearer RED; the tournament hid it by alternating colours, but any
    single-colour experiment was measuring the map, not the agent."""

    DEPLOY_DEPTH = 2

    def test_objectives_equidistant_from_both_deployments(self):
        for map_seed in (1, 2, 3):
            scenario = Scenario.preset("combined_arms", map_seed=map_seed)
            width = scenario.width
            blue_front = self.DEPLOY_DEPTH - 1
            red_front = width - self.DEPLOY_DEPTH
            for coord in scenario.objectives:
                col = coord[0] + coord[1] // 2
                blue_gap = col - blue_front
                red_gap = red_front - col
                self.assertEqual(
                    blue_gap, red_gap,
                    f"objective at col {col} is {blue_gap} from BLUE but "
                    f"{red_gap} from RED (width {width})",
                )

    def test_default_width_is_odd(self):
        """Exact symmetry is only possible when width - 1 is even."""
        self.assertEqual(Scenario().width % 2, 1)


class TestLogistics(unittest.TestCase):
    """Supply is one mechanism producing three behaviours: culmination,
    encirclement, and air sortie cycles. Each is tested separately because
    each can break on its own."""

    def setUp(self):
        self.scenario = Scenario.preset("combined_arms", map_seed=1)
        self.game = Game(self.scenario, seed=0)

    def test_units_start_in_supply_at_their_depots(self):
        for u in self.game.units.values():
            self.assertTrue(u.in_supply, f"{u} starts cut off at its own baseline")
            self.assertEqual(u.supply, 100.0)

    def test_supply_does_not_cover_the_whole_map(self):
        """At a budget of 13 the road net projected supply over 87% of the
        board and the mechanic did nothing at all."""
        net = self.game.supply_net[Side.BLUE][Domain.LAND]
        passable = [c for c in self.game.map.all_coords()
                    if self.game.map.passable(c, Domain.LAND)]
        coverage = len(net) / len(passable)
        self.assertLess(coverage, 0.75, f"supply covers {coverage:.0%} of the map")
        self.assertGreater(coverage, 0.20, f"supply covers only {coverage:.0%}")

    def test_out_of_supply_unit_drains_then_starves(self):
        from fieldbox.sim.supply import STARVATION_LOSS
        game = Game(self.scenario, seed=0)
        unit = next(u for u in game.units_of(Side.BLUE)
                    if u.domain is Domain.LAND)
        # Park it far beyond its own network.
        far = max(game.map.all_coords(), key=lambda c: c[0] + c[1] // 2)
        for other in list(game.units_at(far)):
            game._kill(other)
        game.place(unit, far)
        game._update_supply(Side.BLUE)
        self.assertFalse(unit.in_supply)
        start_supply = unit.supply
        for _ in range(6):
            game._run_logistics(Side.BLUE)
        self.assertLess(unit.supply, start_supply)
        self.assertEqual(unit.supply, 0.0)
        strength_before = unit.strength
        game._run_logistics(Side.BLUE)
        self.assertAlmostEqual(unit.strength, strength_before - STARVATION_LOSS)

    def test_supply_reduces_combat_power(self):
        from fieldbox.sim.supply import supply_factor
        self.assertEqual(supply_factor(100.0), 1.0)
        self.assertLess(supply_factor(0.0), supply_factor(50.0))
        self.assertLessEqual(supply_factor(50.0), 1.0)
        # And it actually reaches the combat maths.
        game = Game(self.scenario, seed=0)
        att = next(u for u in game.units_of(Side.BLUE) if u.domain is Domain.LAND)
        dfn = next(u for u in game.units_of(Side.RED) if u.domain is Domain.LAND)
        full = game._attack_power(att, dfn)
        att.supply = 0.0
        dry = game._attack_power(att, dfn)
        self.assertLess(dry, full, "supply state does not affect attack power")

    def test_enemy_ground_interdicts_the_supply_trace(self):
        """Encirclement: a hex ringed by enemy ground cannot be traced to."""
        from fieldbox.core.hexgrid import neighbors
        from fieldbox.sim.supply import interdicted
        game = Game(self.scenario, seed=0)
        target = next(u for u in game.units_of(Side.BLUE)
                      if u.domain is Domain.LAND)
        blocked = interdicted(game, Side.BLUE)
        self.assertNotIn(
            target.coord, blocked,
            "a friendly-occupied hex should hold the corridor open",
        )
        enemy = next(u for u in game.units_of(Side.RED)
                     if u.domain is Domain.LAND)
        neighbour = next(n for n in neighbors(*enemy.coord)
                         if game.map.in_bounds(n))
        self.assertIn(neighbour, interdicted(game, Side.BLUE))

    def test_in_supply_units_repair_out_of_contact(self):
        game = Game(self.scenario, seed=0)
        unit = next(u for u in game.units_of(Side.BLUE)
                    if u.domain is Domain.LAND)
        unit.strength = 40.0
        game._update_supply(Side.BLUE)
        self.assertTrue(unit.in_supply)
        game._run_logistics(Side.BLUE)
        self.assertGreater(unit.strength, 40.0)

    def test_aircraft_run_dry_far_faster_than_ground(self):
        """The sortie cycle depends on this ratio -- if it narrows, aircraft
        loiter over the objective forever."""
        from fieldbox.sim.units import STATS, UnitType
        air = STATS[UnitType.JET].upkeep
        ground = STATS[UnitType.INFANTRY].upkeep
        self.assertGreater(air, ground * 5)

    def test_culmination_supply_falls_with_distance_advanced(self):
        from fieldbox.agents import DoctrineAgent as DA
        near, far = [], []
        for seed in range(6):
            game = Game(self.scenario, seed=seed)
            agents = {Side.BLUE: DA(), Side.RED: DA()}
            while not game.is_over:
                side = game.current_side
                if side is Side.BLUE:
                    for u in game.units_of(Side.BLUE):
                        if u.domain is not Domain.LAND:
                            continue
                        col = u.q + u.r // 2
                        (near if col <= 5 else far).append(u.supply)
                game.step(agents[side].act(game, side))
        self.assertTrue(near and far, "no data on one side of the split")
        self.assertGreater(
            sum(near) / len(near), sum(far) / len(far),
            "units deep in enemy territory are no worse supplied than at home",
        )


class TestTelemetry(unittest.TestCase):
    """Recording must be pure observation. If attaching the recorder changed
    the game, every number the viewer and the report show would describe a
    different match from the one the tournament played."""

    def setUp(self):
        self.scenario = Scenario.preset("combined_arms", map_seed=1)

    def test_recording_does_not_change_the_game(self):
        from fieldbox.league.arena import play_match
        from fieldbox.record.telemetry import play_recorded
        for seed in range(4):
            plain = play_match(
                DoctrineAgent(), RushAgent(), self.scenario, seed=seed
            )
            game, _ = play_recorded(
                DoctrineAgent(), RushAgent(), self.scenario, seed=seed
            )
            self.assertEqual(plain.winner, game.winner())
            self.assertEqual(plain.turns, game.turn)
            self.assertEqual(plain.vp[Side.BLUE], game.vp[Side.BLUE])
            self.assertEqual(plain.vp[Side.RED], game.vp[Side.RED])

    def test_csv_tables_are_wellformed(self):
        import csv
        import tempfile
        from fieldbox.record.telemetry import play_recorded
        game, rec = play_recorded(
            DoctrineAgent(), RushAgent(), self.scenario, seed=0
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = rec.write_csv(tmp)
            self.assertEqual(len(paths), 3)
            with open(paths[0]) as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), len(rec.turns))
            for field in ("turn", "vp_blue", "cutoff_blue", "net_blue"):
                self.assertIn(field, rows[0])

    def test_one_unit_row_per_living_unit_per_phase(self):
        from fieldbox.record.telemetry import play_recorded
        game, rec = play_recorded(
            DoctrineAgent(), RushAgent(), self.scenario, seed=2
        )
        counts = {}
        for row in rec.units:
            counts[row.phase] = counts.get(row.phase, 0) + 1
        for turn_row in rec.turns:
            self.assertEqual(
                counts.get(turn_row.phase, 0),
                turn_row.units_blue + turn_row.units_red,
            )


class TestDiagnosticReport(unittest.TestCase):
    """The anomaly checks exist because a green test suite caught none of the
    behavioural bugs in this project. They must actually fire."""

    def test_detects_a_wiped_out_unit_type(self):
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"ART": TypeStats(count=20, lost=20, unit_turns=200,
                                  engagements=40)}
        rival = {"ART": TypeStats(count=20, lost=4, unit_turns=200,
                                  engagements=40)}
        flags = find_anomalies(stats, 0.5, 0.5, 20, rival)
        self.assertTrue(any("markedly" in f or "destroyed in 100%" in f
                            for f in flags))

    def test_symmetric_attrition_is_not_an_anomaly(self):
        """Both sides losing every tank is a bloody scenario, not a bug. The
        absolute threshold flagged this in `littoral` while staying silent on
        a real 83-point gap in `combined_arms`."""
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"TNK": TypeStats(count=12, lost=12, unit_turns=200,
                                  engagements=60)}
        rival = {"TNK": TypeStats(count=12, lost=12, unit_turns=200,
                                  engagements=60)}
        self.assertEqual(find_anomalies(stats, 0.5, 0.5, 12, rival), [])

    def test_detects_one_sided_losses(self):
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"ART": TypeStats(count=12, lost=0, unit_turns=200,
                                  engagements=60)}
        rival = {"ART": TypeStats(count=12, lost=10, unit_turns=200,
                                  engagements=60)}
        flags = find_anomalies(stats, 0.5, 0.5, 12, rival)
        self.assertTrue(any("markedly" in f for f in flags))

    def test_detects_a_unit_that_neither_fights_nor_holds_ground(self):
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"RCN": TypeStats(count=20, lost=2, unit_turns=400,
                                  engagements=1, objective_turns=0)}
        flags = find_anomalies(stats, 0.5, 0.5, 20)
        self.assertTrue(any("neither fighting nor scoring" in f for f in flags))

    def test_defensive_units_holding_ground_are_not_flagged(self):
        """Infantry sitting on an objective for thirty turns is doing its
        job. Counting only engagements flagged it as idle."""
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"INF": TypeStats(count=20, lost=4, unit_turns=400,
                                  engagements=3, objective_turns=260)}
        self.assertEqual(find_anomalies(stats, 0.5, 0.5, 20), [])

    def test_detects_a_unit_stuck_on_its_deployment_hex(self):
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"SHP": TypeStats(count=10, lost=1, unit_turns=200,
                                  engagements=20, stuck_turns=180)}
        flags = find_anomalies(stats, 0.5, 0.5, 10)
        self.assertTrue(any("deployment hex" in f for f in flags))

    def test_detects_colour_dependence(self):
        """Playing first vs second decided this matchup entirely -- the bug
        that a single-colour batch hides."""
        from fieldbox.record.report import find_anomalies
        flags = find_anomalies({}, 0.0, 1.0, 16)
        self.assertTrue(any("colour dependence" in f for f in flags))

    def test_clean_stats_produce_no_flags(self):
        from fieldbox.record.report import TypeStats, find_anomalies
        stats = {"INF": TypeStats(count=20, lost=6, unit_turns=400,
                                  engagements=90, stuck_turns=40,
                                  cutoff_turns=30)}
        self.assertEqual(find_anomalies(stats, 0.55, 0.45, 20), [])

    def test_harness_does_not_misconfigure_agents(self):
        """`try: cls(seed) except TypeError` passed the game index straight
        into DoctrineAgent(aggression=...), so a batch silently compared
        eight different doctrines and blamed the result on the units."""
        from fieldbox.agents import DoctrineAgent, RandomAgent
        from fieldbox.record.report import run
        import fieldbox.record.report as R
        made = []
        original = R.play_recorded

        def spy(blue, red, scenario, seed=0, **kw):
            made.append((blue, red))
            return original(blue, red, scenario, seed=seed, **kw)

        R.play_recorded = spy
        try:
            run("legacy", "doctrine", "random", games=4, map_seeds=(1,))
        finally:
            R.play_recorded = original
        doctrines = [a for pair in made for a in pair
                     if isinstance(a, DoctrineAgent)]
        self.assertTrue(doctrines)
        for agent in doctrines:
            self.assertEqual(
                agent.aggression, 1.0,
                "the batch harness is mutating the agent it is measuring",
            )
        randoms = [a for pair in made for a in pair
                   if isinstance(a, RandomAgent)]
        self.assertTrue(randoms, "RandomAgent should still receive a seed")

    def test_report_runs_end_to_end(self):
        from fieldbox.record.report import run
        text = run("legacy", "doctrine", "hold", games=4, map_seeds=(1,))
        self.assertIn("FIELDBOX DIAGNOSTIC", text)
        self.assertIn("playing first", text)
        self.assertIn("Culmination", text)


class TestGoldenHash(unittest.TestCase):
    """Pins the outcome of a fixed battery of matches.

    Performance refactors must not change results. If you deliberately change
    combat balance, recompute this hash -- but never update it to make a
    'harmless' optimisation pass.
    """

    # Re-baselined deliberately at v0.1.1, v0.1.2, v0.2.0, v0.3.0, v0.4.1
    # (air standoff + flank security) and v0.4.2 (exposure net of friendly
    # support). Never update this to make a performance refactor pass --
    # that is the case it exists to catch.
    GOLDEN = "01f634df7b7374e7510aaebdf8f11f7f0b6c7ed77283ae4b68dda11eb0578eb0"

    def test_outcomes_unchanged(self):
        import hashlib
        from fieldbox.agents import HoldAgent

        h = hashlib.sha256()
        for map_seed in (1, 2, 3):
            scenario = Scenario.preset("combined_arms", map_seed=map_seed)
            for seed in range(5):
                for a, b in (
                    (DoctrineAgent(), RushAgent()),
                    (HoldAgent(), DoctrineAgent()),
                ):
                    r = play_match(a, b, scenario, seed=seed)
                    h.update(
                        f"{r.winner}|{r.vp[Side.BLUE]:.3f}|{r.vp[Side.RED]:.3f}"
                        f"|{r.turns}|{r.survivors[Side.BLUE]}"
                        f"|{r.survivors[Side.RED]}".encode()
                    )
        self.assertEqual(
            h.hexdigest(), self.GOLDEN,
            "game outcomes changed -- intentional balance edit, or a bug?",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
