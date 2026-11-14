import random
import subprocess
import sys
import unittest

from fieldbox.core.rng import derive_seed, make_rng


class TestRng(unittest.TestCase):
    def test_same_parts_same_seed(self):
        self.assertEqual(derive_seed("world", 7, "rivers"),
                         derive_seed("world", 7, "rivers"))

    def test_parts_matter(self):
        seeds = {derive_seed("world", 7), derive_seed("world", 8),
                 derive_seed("combat", 7), derive_seed("world", "7"),
                 derive_seed(("world", 7))}
        self.assertEqual(len(seeds), 5)

    def test_seed_is_64_bit_unsigned(self):
        s = derive_seed("x")
        self.assertGreaterEqual(s, 0)
        self.assertLess(s, 2 ** 64)

    def test_pinned_value(self):
        # Pinned so a refactor cannot silently change every stream.
        self.assertEqual(derive_seed("fieldbox", 0), PINNED)

    def test_stable_across_processes(self):
        code = ("from fieldbox.core.rng import derive_seed;"
                "print(derive_seed('world', 3, 'cities'))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True).stdout.strip()
        self.assertEqual(int(out), derive_seed("world", 3, "cities"))

    def test_make_rng_streams(self):
        a = [make_rng("m", 1).random() for _ in range(3)]
        self.assertEqual(a[0], a[1])
        self.assertIsInstance(make_rng("m"), random.Random)
        self.assertNotEqual(make_rng("m", 1).random(), make_rng("m", 2).random())


PINNED = 16129785621874226527
