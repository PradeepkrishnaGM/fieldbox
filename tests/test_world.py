"""World generation: symmetry, validity, provinces, placement, determinism."""

import os
import subprocess
import sys
import unittest

from fieldbox import params as P
from fieldbox.core.hexgrid import (Domain, HexMap, Terrain, col_of, distance,
                                   mirror_coord, neighbors)
from fieldbox.sim.units import Side
from fieldbox.world import WorldGenError, generate_world
from fieldbox.world.placement import open_seas

from _util import slow

_CACHE = {}


def world(seed, preset="tiny"):
    key = (seed, preset)
    if key not in _CACHE:
        _CACHE[key] = generate_world(seed, preset)
    return _CACHE[key]


QUICK = [(s, "tiny") for s in range(4)] + [(1, "standard")]


class TestParams(unittest.TestCase):
    def test_odd_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            P.WorldParams(width=47)
        with self.assertRaises(ValueError):
            P.WorldParams(height=31)

    def test_presets(self):
        self.assertEqual(set(P.PRESETS), {"tiny", "standard", "large"})
        self.assertEqual((P.PRESETS["tiny"].world.width, P.PRESETS["tiny"].world.height), (24, 16))
        self.assertEqual(P.PRESETS["tiny"].world.min_home_provinces, 2)
        self.assertEqual(P.PRESETS["large"].world.provinces_per_half, 12)
        tiny = dict(P.PRESETS["tiny"].start.forces)
        self.assertTrue(all(n >= 1 for n in tiny.values()))
        self.assertEqual(dict(P.PRESETS["large"].start.forces)["INFANTRY"], 6)


class TestTerrainRules(unittest.TestCase):
    def test_mountain_constants(self):
        m = HexMap(4, 4)
        m.set_terrain((0, 0), Terrain.MOUNTAIN)
        self.assertEqual(m.move_cost((0, 0), Domain.LAND), 3.0)
        self.assertEqual(m.def_mult((0, 0)), 1.6)
        self.assertFalse(m.passable((0, 0), Domain.SEA))

    def test_river_adds_cost_and_bridge_removes_it(self):
        m = HexMap(6, 6)
        c = (1, 1)
        self.assertEqual(m.move_cost(c), 1.0)
        m.add_river(c)
        self.assertEqual(m.move_cost(c), 2.0)           # +1 for the river
        m.add_road(c)
        self.assertEqual(m.move_cost(c), 0.5)           # bridge: road cost, no penalty
        self.assertEqual(m.move_cost(c, Domain.AIR), 1.0)   # rivers do not slow aircraft

    def test_tanks_attack_mountains_at_half(self):
        from fieldbox.sim.units import ATTACK_TERRAIN_MOD, UnitType
        self.assertEqual(ATTACK_TERRAIN_MOD[UnitType.TANK][Terrain.MOUNTAIN], 0.5)
        self.assertEqual(ATTACK_TERRAIN_MOD[UnitType.IFV][Terrain.MOUNTAIN], 0.5)


class TestSymmetry(unittest.TestCase):
    def test_mirror_is_an_involution_preserving_adjacency(self):
        for preset in ("tiny", "standard", "large"):
            wp = P.PRESETS[preset].world
            m = HexMap(wp.width, wp.height)
            for c in m.terrain:
                mc = mirror_coord(c, wp.width, wp.height)
                self.assertIn(mc, m.terrain)
                self.assertEqual(mirror_coord(mc, wp.width, wp.height), c)
                self.assertEqual(col_of(c) + col_of(mc), wp.width - 1)
                for n in neighbors(*c):
                    if n in m.terrain:
                        self.assertEqual(distance(mc, mirror_coord(n, wp.width, wp.height)), 1)

    def check_symmetric(self, w):
        t, mir = w.hexmap.terrain, w.mirror
        for c, k in t.items():
            self.assertEqual(t[mir(c)], k, f"terrain {c}")
        self.assertEqual({mir(c) for c in w.hexmap.rivers}, w.hexmap.rivers)
        self.assertEqual({mir(c) for c in w.hexmap.roads}, w.hexmap.roads)
        self.assertEqual({frozenset(mir(c) for c in city) for city in w.cities},
                         {frozenset(city) for city in w.cities})
        for c, pid in w.province_of.items():
            self.assertEqual(w.province_of[mir(c)], w.mirror_province(pid), f"province {c}")
        for pid, rid in w.region_of.items():
            self.assertEqual(w.region_of[w.mirror_province(pid)], w.mirror_region(rid))
        for p in w.provinces:
            q = w.provinces[w.mirror_province(p.id)]
            self.assertEqual(q.capital, mir(p.capital))
            self.assertEqual((q.value, q.has_city), (p.value, p.has_city))
        self.assertEqual(w.hq[Side.RED], mir(w.hq[Side.BLUE]))
        for side in Side:
            self.assertEqual(
                sorted((s.kind, mir(s.coord)) for s in w.structures if s.side is side),
                sorted((s.kind, s.coord) for s in w.structures if s.side is side.other))
            self.assertEqual(
                sorted((u.kind, mir(u.coord)) for u in w.units if u.side is side),
                sorted((u.kind, u.coord) for u in w.units if u.side is side.other))
        for pid, own in w.owner.items():
            mo = w.owner[w.mirror_province(pid)]
            self.assertEqual(None if own is None else own.other, mo)

    def test_all_layers_symmetric_quick(self):
        for seed, preset in QUICK:
            with self.subTest(seed=seed, preset=preset):
                self.check_symmetric(world(seed, preset))

    @slow
    def test_all_layers_symmetric_many(self):
        for preset, seeds in (("tiny", range(50)), ("standard", range(20)), ("large", range(5))):
            for seed in seeds:
                with self.subTest(seed=seed, preset=preset):
                    self.check_symmetric(world(seed, preset))


class TestValidity(unittest.TestCase):
    def check_valid(self, w):
        from fieldbox.world.generate import validate
        self.assertIsNone(validate(w))

    def test_tiny_seeds_0_49_all_generate(self):
        for seed in range(50):
            self.check_valid(world(seed, "tiny"))

    @slow
    def test_standard_seeds_0_49_all_generate(self):
        retries = []
        for seed in range(50):
            w = world(seed, "standard")
            self.check_valid(w)
            retries.append(w.retries)
        self.assertLessEqual(sum(retries) / len(retries), 3.0)

    @slow
    def test_large_seeds_0_9_all_generate(self):
        for seed in range(10):
            self.check_valid(world(seed, "large"))

    def test_failure_raises(self):
        import fieldbox.world.generate as G
        old = G.MAX_TRIES
        G.MAX_TRIES = 1
        try:
            bad = P.Params(world=P.WorldParams(water_fraction=0.95))
            with self.assertRaises(WorldGenError):
                generate_world(0, "standard", bad)
        finally:
            G.MAX_TRIES = old

    def test_central_sea_is_its_own_mirror(self):
        for seed, preset in QUICK:
            w = world(seed, preset)
            self.assertTrue(open_seas(w), f"no open sea for {seed} {preset}")


class TestProvinces(unittest.TestCase):
    def test_provinces_partition_all_land(self):
        for seed, preset in QUICK:
            w = world(seed, preset)
            land = set(w.land_hexes())
            seen = []
            for p in w.provinces:
                seen += list(p.hexes)
            self.assertEqual(len(seen), len(set(seen)), "a hex is in two provinces")
            self.assertEqual(set(seen), land)
            self.assertEqual(set(w.province_of), land)
            for p in w.provinces:
                self.assertTrue(p.hexes)
                self.assertIn(p.capital, p.hexes)

    def test_province_adjacency_symmetric_and_real(self):
        for seed, preset in QUICK:
            w = world(seed, preset)
            for p in w.provinces:
                self.assertNotIn(p.id, p.neighbours)
                for q in p.neighbours:
                    self.assertIn(p.id, w.provinces[q].neighbours)
                    self.assertTrue(any(n in set(w.provinces[q].hexes)
                                        for h in p.hexes for n in neighbors(*h)))

    def test_regions_partition_provinces(self):
        for seed, preset in QUICK:
            w = world(seed, preset)
            wp = w.params.world
            members = [p for r in w.regions for p in r.provinces]
            self.assertEqual(sorted(members), list(range(len(w.provinces))))
            for r in w.regions:
                self.assertLessEqual(len(r.provinces), wp.region_size + 1)
                self.assertEqual(r.bonus, wp.region_bonus)

    def test_province_values(self):
        w = world(1, "standard")
        wp = w.params.world
        for p in w.provinces:
            self.assertEqual(p.value, wp.province_base_value + (wp.city_value if p.has_city else 0))


class TestPlacement(unittest.TestCase):
    def test_start_positions(self):
        for seed, preset in QUICK:
            w = world(seed, preset)
            wp, st = w.params.world, w.params.start
            t = w.hexmap.terrain
            self.assertEqual(len(w.home[Side.BLUE]), wp.min_home_provinces)
            owned = [pid for pid, o in w.owner.items() if o is not None]
            self.assertEqual(len(owned), 2 * wp.min_home_provinces)
            self.assertTrue(any(o is None for o in w.owner.values()), "no NEUTRAL province")
            hq = w.hq[Side.BLUE]
            self.assertFalse(any(t[n] is Terrain.WATER for n in neighbors(*hq) if n in t))
            for side in Side:
                kinds = sorted(s.kind for s in w.structures if s.side is side)
                want = ["HQ"] + [k for k, n in st.structures for _ in range(n)]
                self.assertEqual(kinds, sorted(want))
                coords = [s.coord for s in w.structures if s.side is side]
                self.assertEqual(len(coords), len(set(coords)))
                for s in w.structures:
                    if s.kind == "PORT":
                        self.assertTrue(any(t[n] is Terrain.WATER for n in neighbors(*s.coord) if n in t))
                    self.assertNotEqual(t[s.coord], Terrain.WATER)
                counts = {}
                for u in w.units:
                    if u.side is side:
                        counts[u.kind] = counts.get(u.kind, 0) + 1
                self.assertEqual(counts, dict(st.forces))

    def test_one_unit_per_domain_per_hex_and_legal_terrain(self):
        w = world(1, "standard")
        t = w.hexmap.terrain
        seen = set()
        for u in w.units:
            dom = "AIR" if u.kind in ("HELICOPTER", "JET") else "SEA" if u.kind == "SHIP" else "LAND"
            self.assertNotIn((dom, u.coord), seen)
            seen.add((dom, u.coord))
            self.assertEqual(w.half(u.coord), u.side)
            if dom == "LAND":
                self.assertNotEqual(t[u.coord], Terrain.WATER)
            if dom == "SEA":
                self.assertEqual(t[u.coord], Terrain.WATER)

    def test_home_territory_is_connected_by_land_to_the_enemy(self):
        w = world(2, "tiny")
        t = w.hexmap.terrain
        seen, stack = {w.hq[Side.BLUE]}, [w.hq[Side.BLUE]]
        while stack:
            for n in neighbors(*stack.pop()):
                if n in t and n not in seen and t[n] is not Terrain.WATER:
                    seen.add(n)
                    stack.append(n)
        self.assertIn(w.hq[Side.RED], seen)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_world(self):
        a, b = generate_world(7, "tiny"), generate_world(7, "tiny")
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_different_seeds_differ(self):
        prints = {generate_world(s, "tiny").fingerprint() for s in range(6)}
        self.assertEqual(len(prints), 6)

    def test_stable_across_processes_and_hash_seeds(self):
        code = ("from fieldbox.world import generate_world;"
                "print(generate_world(5, 'tiny').fingerprint())")
        outs = []
        for hs in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=hs)
            outs.append(subprocess.run([sys.executable, "-c", code], capture_output=True,
                                       text=True, check=True, env=env).stdout.strip())
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], generate_world(5, "tiny").fingerprint())


if __name__ == "__main__":
    unittest.main()
