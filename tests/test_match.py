"""M5: replays and the match screen. UI tests need pygame-ce (use the venv)."""

import gzip
import json
import os
import tempfile
import unittest

from fieldbox import Side, generate_world
from fieldbox.agents import DoctrineAgent, RandomAgent, RushAgent
from fieldbox.league.arena import run_game
from fieldbox.record.replay import LiveMatch, Replay, ReplayError, record_game
from fieldbox.sim import events as E

try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    HAVE_PYGAME = True
except ImportError:      # pragma: no cover
    HAVE_PYGAME = False


def played(seed=2, max_turns=14):
    w = generate_world(1, "tiny")
    return w, record_game(DoctrineAgent(), RushAgent(), w, seed=seed, max_turns=max_turns)


class TestReplay(unittest.TestCase):
    def test_recording_does_not_change_the_game(self):
        w = generate_world(1, "tiny")
        for seed in range(3):
            plain = run_game(DoctrineAgent(), RushAgent(), w, seed=seed, max_turns=15)
            game, _ = record_game(DoctrineAgent(), RushAgent(), w, seed=seed, max_turns=15)
            self.assertEqual(E.stream_hash(plain.events), E.stream_hash(game.events))

    def test_one_snapshot_per_turn_plus_deployment(self):
        w, (game, rep) = played()
        self.assertEqual(len(rep.snapshots), game.turn + 1)
        self.assertEqual(len(rep.events), len(rep.snapshots))
        self.assertEqual(rep.snapshots[0]["turn"], 0)
        self.assertEqual(rep.events[0], [])
        self.assertTrue(rep.finished)
        self.assertEqual(rep.meta["result"]["turns"], game.turn)

    def test_final_snapshot_matches_the_game(self):
        w, (game, rep) = played()
        last = rep.snapshots[-1]
        self.assertEqual(len(last["units"]), len(game.units))
        self.assertEqual(last["vp"], [game.vp[Side.BLUE], game.vp[Side.RED]])
        self.assertEqual(last["pp"], [round(game.pp[Side.BLUE], 1), round(game.pp[Side.RED], 1)])
        self.assertEqual(len(last["structures"]), len(game.structures))

    def test_round_trip_through_a_file(self):
        w, (game, rep) = played()
        with tempfile.TemporaryDirectory() as tmp:
            path = rep.save(os.path.join(tmp, "sub", "a.fbr"))
            back = Replay.load(path)
        self.assertEqual(back.to_dict(), rep.to_dict())
        self.assertEqual(back.world().fingerprint(), w.fingerprint())

    def test_file_is_gzip_json_with_a_version(self):
        w, (game, rep) = played()
        with tempfile.TemporaryDirectory() as tmp:
            path = rep.save(os.path.join(tmp, "a.fbr"))
            with gzip.open(path, "rt") as fh:
                d = json.load(fh)
        self.assertEqual(d["version"], 1)

    def test_unknown_version_and_bad_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "v.fbr")
            with gzip.open(bad, "wt") as fh:
                json.dump({"version": 99, "meta": {}, "snapshots": [], "events": []}, fh)
            with self.assertRaises(ReplayError):
                Replay.load(bad)
            junk = os.path.join(tmp, "j.fbr")
            with open(junk, "w") as fh:
                fh.write("not gzip")
            with self.assertRaises(ReplayError):
                Replay.load(junk)

    def test_changed_generator_is_detected(self):
        w, (game, rep) = played()
        rep.meta["fingerprint"] = "0" * 64
        with self.assertRaises(ReplayError):
            rep.world()

    def test_live_match_steps_one_turn_at_a_time(self):
        m = LiveMatch(RandomAgent(1), RandomAgent(2), generate_world(1, "tiny"), seed=1, max_turns=5)
        self.assertEqual(len(m.replay), 1)
        m.step()
        self.assertEqual(len(m.replay), 2)
        while not m.done:
            m.step()
        self.assertEqual(len(m.replay), 6)
        m.step()                                  # no-op after the end
        self.assertEqual(len(m.replay), 6)

    def test_moved_events_carry_paths_the_viewer_can_animate(self):
        w, (game, rep) = played()
        moved = [e for evs in rep.events for e in evs if e["k"] == "moved"]
        self.assertTrue(moved)
        self.assertTrue(all(len(e["d"]["path"]) >= 2 for e in moved))


@unittest.skipUnless(HAVE_PYGAME, "pygame-ce not installed")
class TestMatchScreen(unittest.TestCase):
    def test_renders_turn_0_turn_10_and_the_end(self):
        from fieldbox.ui.shot import render_screen
        with tempfile.TemporaryDirectory() as tmp:
            for name, turns in (("t0", 0), ("t10", 10), ("end", 999)):
                out = render_screen("match", os.path.join(tmp, f"{name}.png"), seed=1,
                                    preset="tiny", turns=turns)
                self.assertTrue(os.path.getsize(out) > 5000)

    def test_renders_a_saved_replay_and_mid_animation(self):
        from fieldbox.ui.shot import render_screen
        w, (game, rep) = played()
        with tempfile.TemporaryDirectory() as tmp:
            path = rep.save(os.path.join(tmp, "a.fbr"))
            out = render_screen("match", os.path.join(tmp, "r.png"), replay=path, turns=8, anim=0.7)
            self.assertTrue(os.path.getsize(out) > 5000)

    def _screen(self):
        from fieldbox.ui.app import App
        from fieldbox.ui.screens.match import MatchScreen
        app = App((1600, 900))
        self.addCleanup(pygame.quit)
        scr = MatchScreen.create(app, seed=1, preset="tiny", blue="doctrine", red="rush", turns=12)
        app.push(scr)
        return scr

    def test_live_mode_plays_forward_and_stops_at_the_end(self):
        scr = self._screen()
        scr.set_speed(4)                          # max: no animation waits
        for _ in range(200):
            scr.update(0.05)
        self.assertEqual(scr.idx, 12)
        self.assertTrue(scr.rep.finished)
        self.assertFalse(scr.playing)
        scr.draw(scr.app.window)                  # end overlay draws

    def test_scrubbing_and_stepping_over_played_turns(self):
        scr = self._screen()
        scr.advance_to(8)
        self.assertEqual(scr.idx, 8)
        scr.step(-1)
        self.assertEqual(scr.idx, 7)
        self.assertFalse(scr.playing)
        scr.goto(0)
        self.assertEqual(scr.idx, 0)
        scr.goto(10_000)                          # clamped to what has been played
        self.assertEqual(scr.idx, scr.last)

    def test_selection_card_and_log_filters(self):
        scr = self._screen()
        scr.advance_to(6)
        uid = scr.snap["units"][0][0]
        scr.select = ("unit", uid)
        self.assertTrue(any("strength" in t for t, _ in scr._card_lines()))
        sid = scr.snap["structures"][0][0]
        scr.select = ("structure", sid)
        self.assertTrue(any("strength" in t for t, _ in scr._card_lines()))
        scr.filter = "all"
        n_all = len(scr._log_lines())
        scr.filter = "combat"
        self.assertLessEqual(len(scr._log_lines()), n_all)
        scr.draw(scr.app.window)

    def test_pick_finds_a_unit_under_the_cursor(self):
        scr = self._screen()
        scr.update(0.0)
        row = scr.snap["units"][0]
        x, y = scr._unit_screen(row)
        self.assertEqual(scr._pick((x, y)), ("unit", row[0]))


if __name__ == "__main__":
    unittest.main()
