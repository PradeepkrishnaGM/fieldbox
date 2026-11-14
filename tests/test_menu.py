"""M9: settings, roster, menu and setup screens. UI tests need pygame-ce (use the venv)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from fieldbox import settings
from fieldbox.league import roster

try:
    import pygame
except ImportError:                                  # pragma: no cover
    pygame = None


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("FIELDBOX_CONFIG")
        os.environ["FIELDBOX_CONFIG"] = str(Path(self.tmp.name) / "sub" / "settings.json")
        self.addCleanup(lambda: os.environ.__setitem__("FIELDBOX_CONFIG", self.old) if self.old
                        else os.environ.pop("FIELDBOX_CONFIG", None))

    def test_missing_file_gives_defaults(self):
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_round_trip_and_creates_directory(self):
        s = {"window": [1280, 720], "anim_speed": 2.0}
        self.assertTrue(settings.save(s))
        self.assertEqual(settings.load(), s)

    def test_broken_or_odd_files_fall_back(self):
        p = settings.path()
        p.parent.mkdir(parents=True)
        p.write_text("{not json")
        self.assertEqual(settings.load(), settings.DEFAULTS)
        p.write_text(json.dumps({"window": [10, 10], "anim_speed": 99}))
        s = settings.load()
        self.assertGreaterEqual(s["window"][0], 1280)           # clamped to the minimum window
        self.assertEqual(s["anim_speed"], 1.0)

    def test_unwritable_location_does_not_raise(self):
        os.environ["FIELDBOX_CONFIG"] = "/proc/nope/settings.json"
        self.assertFalse(settings.save(settings.DEFAULTS))


class TestRoster(unittest.TestCase):
    def test_builtin_and_champions(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(roster.names(root), list(roster.BUILTIN))
            run = Path(root) / "r1"
            run.mkdir()
            (run / "meta.json").write_text("{}")
            from fieldbox.agents import DoctrineGenome
            g = DoctrineGenome.default().with_(aggression=0.9).to_dict()
            (run / "gen_003.json").write_text(json.dumps({"champion_genome": g}))
            self.assertIn("champion:r1:3", roster.names(root))
            a = roster.make("champion:r1:3", root)
            self.assertEqual(a.genome["aggression"], 0.9)
            with self.assertRaises(SystemExit):
                roster.make("champion:r1:9", root)
            with self.assertRaises(SystemExit):
                roster.make("nonsense", root)
        self.assertEqual(roster.make("rush").name, "rush")


@unittest.skipIf(pygame is None, "needs pygame-ce")
class TestScreens(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from fieldbox.ui.app import App
        self.app = App((1600, 900))
        self.addCleanup(pygame.quit)

    def test_every_screen_renders(self):
        from fieldbox.ui.app import make_screen
        for name in ("menu", "setup", "mapview", "symbols", "evolab"):
            scr = make_screen(name, self.app, 1, "tiny", root="/nonexistent")
            self.app.push(scr)
            self.app.draw_frame()
            self.app.pop()

    def test_menu_buttons_open_screens(self):
        from fieldbox.ui.screens.menu import MenuScreen, ReplayPicker
        from fieldbox.ui.screens.setup import SetupScreen
        menu = MenuScreen(self.app)
        self.app.push(menu)
        self.app.draw_frame()
        self.assertEqual(len(menu.buttons), 8)
        menu.new_match()
        self.assertIsInstance(self.app.top, SetupScreen)
        self.app.draw_frame()
        self.app.pop()
        menu.watch_replay()
        self.assertIsInstance(self.app.top, ReplayPicker)
        self.app.draw_frame()
        self.app.pop()
        menu.quit()
        self.assertFalse(self.app.running)

    def test_setup_starts_a_playable_match(self):
        from fieldbox.ui.screens.match import MatchScreen
        from fieldbox.ui.screens.setup import SetupScreen
        s = SetupScreen(self.app, root="/nonexistent")
        self.app.push(s)
        s.cycle_preset(); s.cycle_preset(); s.cycle_preset()             # back to tiny
        s.add_seed(2); s.add_turns(-1); s.add_game_seed(1)
        s.pick("blue", 1)
        self.assertEqual((s.preset, s.map_seed, s.turns, s.game_seed), ("tiny", 3, 25, 1))
        s.start()
        top = self.app.top
        self.assertIsInstance(top, MatchScreen)
        self.assertEqual(top.rep.meta["max_turns"], 25)
        self.assertEqual(top.rep.meta["map_seed"], 3)
        top.set_speed(4)
        for _ in range(60):
            top.update(0.05)
        self.assertGreater(top.idx, 0)

    def test_settings_buttons_save(self):
        from fieldbox.ui.screens.menu import MenuScreen
        with tempfile.TemporaryDirectory() as d:
            os.environ["FIELDBOX_CONFIG"] = os.path.join(d, "s.json")
            self.addCleanup(os.environ.pop, "FIELDBOX_CONFIG", None)
            m = MenuScreen(self.app)
            m.cycle_speed()
            self.assertEqual(settings.load()["anim_speed"], 2.0)
