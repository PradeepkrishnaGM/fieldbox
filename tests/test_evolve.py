"""M8: evolution league (evo-tiny only; the real preset is run by hand)."""

import json
import tempfile
import unittest
from pathlib import Path

from fieldbox.league import evolve as E
from fieldbox.league.stats import expected, performance_rating, winrate_matrix

from _util import slow


def _files(d: Path):
    names = sorted(p.name for p in d.glob("gen_*.json")) + ["xgen.json"]
    return {n: (d / n).read_bytes() for n in names}


class TestStats(unittest.TestCase):
    def test_performance_rating(self):
        r = performance_rating([(1500.0, 0.5)] * 10)
        self.assertAlmostEqual(r, 1500.0, delta=0.5)
        self.assertGreater(performance_rating([(1500.0, 1.0)] * 10), 1500.0)
        self.assertLess(performance_rating([(1500.0, 0.0)] * 10), 1500.0)
        self.assertTrue(0 < performance_rating([(1500.0, 1.0)] * 3) < 4000)   # finite at 3/3
        self.assertAlmostEqual(expected(1600, 1500) + expected(1500, 1600), 1.0)

    def test_matrix(self):
        m = winrate_matrix({(0, 1): [1.0, 0.0], (1, 0): [0.0, 1.0]}, 2)
        self.assertEqual(m, [[0.5, 0.5], [0.5, 0.5]])


@slow
class TestEvolveTiny(unittest.TestCase):
    def test_worker_count_does_not_change_the_run(self):
        with tempfile.TemporaryDirectory() as root:
            E.evolve("evo-tiny", "w1", 5, root=root, workers=1, log=lambda s: None)
            E.evolve("evo-tiny", "w2", 5, root=root, workers=2, log=lambda s: None)
            self.assertEqual(_files(Path(root) / "w1"), _files(Path(root) / "w2"))

    def test_resume_equals_uninterrupted(self):
        with tempfile.TemporaryDirectory() as root:
            E.evolve("evo-tiny", "full", 7, root=root, workers=1, log=lambda s: None)
            E.evolve("evo-tiny", "cut", 7, root=root, workers=1, stop_after=1, log=lambda s: None)
            self.assertFalse((Path(root) / "cut" / "xgen.json").exists())
            E.evolve("evo-tiny", "cut", 7, root=root, workers=1, resume=True, log=lambda s: None)
            a, b = _files(Path(root) / "full"), _files(Path(root) / "cut")
            self.assertEqual(a, b)
            rep = lambda r: (Path(root) / r / "replays" / "gen_001.fbr").read_bytes()
            self.assertEqual(len(rep("full")), len(rep("cut")))

    def test_files_and_status_well_formed(self):
        with tempfile.TemporaryDirectory() as root:
            d = E.evolve("evo-tiny", "s", 2, root=root, workers=1, log=lambda s: None)
            st = json.loads((d / "status.json").read_text())
            for k in ("run", "state", "gen", "generations", "done", "total", "eta_s", "elapsed_s"):
                self.assertIn(k, st)
            self.assertEqual(st["state"], "done")
            self.assertEqual(st["done"], st["total"])
            g0 = json.loads((d / "gen_000.json").read_text())
            n = len(g0["population"])
            self.assertEqual((len(g0["fitness"]), len(g0["next_population"])), (n, n))
            self.assertIn(g0["champion"], range(n))
            self.assertTrue(all(0.0 <= v <= 1.0 for g in g0["population"] for v in g.values()))
            self.assertGreater(sum(g0["outcomes"].values()), 0)
            x = json.loads((d / "xgen.json").read_text())
            self.assertEqual(len(x["matrix"]), len(x["gens"]))
            self.assertTrue((d / "replays" / "gen_000.fbr").exists())

    def test_existing_run_needs_resume(self):
        with tempfile.TemporaryDirectory() as root:
            E.evolve("evo-tiny", "x", 1, root=root, workers=1, log=lambda s: None)
            with self.assertRaises(SystemExit):
                E.evolve("evo-tiny", "x", 1, root=root, workers=1, log=lambda s: None)


try:
    import pygame
except ImportError:                                  # pragma: no cover
    pygame = None


@unittest.skipIf(pygame is None, "needs pygame-ce")
class TestCharts(unittest.TestCase):
    def setUp(self):
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        from fieldbox.ui import theme
        theme.reset_fonts()
        self.addCleanup(pygame.quit)
        self.surf = pygame.Surface((500, 300))

    def test_charts_draw_something_and_handle_empty_data(self):
        from fieldbox.ui import charts
        r = pygame.Rect(0, 0, 500, 300)
        for draw in (
            lambda: charts.line_chart(self.surf, r, "t", {"a": [0.1, 0.5, 0.9], "b": [0.2, 0.2, 0.3]}),
            lambda: charts.heatmap(self.surf, r, "t", [[0.5, 1.0], [0.0, 0.5]], ["g0", "g1"]),
            lambda: charts.stacked_bar(self.surf, r, "t", [{"x": 3, "y": 1}, {"x": 1, "y": 1}], ("x", "y")),
        ):
            self.surf.fill((0, 0, 0))
            plot = draw()
            self.assertGreater(plot.w, 0)
            self.assertGreater(len({self.surf.get_at((x, y))[:3] for x in range(0, 500, 7)
                                    for y in range(0, 300, 7)}), 3)
        for draw in (lambda: charts.line_chart(self.surf, r, "t", {}),
                     lambda: charts.heatmap(self.surf, r, "t", None, []),
                     lambda: charts.stacked_bar(self.surf, r, "t", [], ("x",))):
            draw()                                                # must not raise

    def test_index_at(self):
        from fieldbox.ui import charts
        plot = pygame.Rect(100, 0, 400, 100)
        self.assertEqual([charts.index_at(plot, 4, x) for x in (100, 199, 200, 499)], [0, 0, 1, 3])
        self.assertIsNone(charts.index_at(plot, 4, 50))


@slow
@unittest.skipIf(pygame is None, "needs pygame-ce")
class TestEvoLab(unittest.TestCase):
    def test_screen_reads_a_run_and_opens_replays(self):
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from fieldbox.ui.app import App
        from fieldbox.ui.screens.evolab import EvoLabScreen
        from fieldbox.ui.screens.match import MatchScreen
        with tempfile.TemporaryDirectory() as root:
            E.evolve("evo-tiny", "r1", 3, root=root, workers=1, log=lambda s: None)
            app = App((1600, 900))
            self.addCleanup(pygame.quit)
            scr = EvoLabScreen(app, root=root)
            app.push(scr)
            self.assertEqual((scr.run, len(scr.gens)), ("r1", 2))
            self.assertIsNotNone(scr.xgen)
            app.draw_frame()                                      # builds the buttons
            scr.sel_gen = 0
            scr.watch_replay()
            self.assertIsInstance(app.top, MatchScreen)
            app.pop()
            scr.play_vs_default()
            self.assertIsInstance(app.top, MatchScreen)
            self.assertTrue(app.top.live is not None)
            app.pop()
            app.draw_frame()
            scr._adjust("pop", 1)
            self.assertEqual(scr.f_pop, 6)
