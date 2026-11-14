"""UI smoke tests. Skipped when pygame-ce is not installed (the engine is
stdlib-only); run them with the project venv: .venv/bin/python -m unittest ..."""

import os
import tempfile
import unittest

from fieldbox.ui.camera import Camera

try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    HAVE_PYGAME = True
except ImportError:      # pragma: no cover
    HAVE_PYGAME = False


class TestCamera(unittest.TestCase):
    def test_hex_pixel_round_trip(self):
        cam = Camera(20, 0.35, 3.0)
        cam.zoom, cam.ox, cam.oy = 1.35, 123.0, 45.0
        for c in [(0, 0), (5, 3), (-2, 7), (20, 15), (3, 30)]:
            x, y = cam.hex_to_screen(c)
            self.assertEqual(cam.screen_to_hex(x, y), c)
            self.assertEqual(cam.screen_to_hex(x + 3, y - 2), c)   # inside the hex

    def test_zoom_is_snapped_clamped_and_anchored(self):
        cam = Camera(20, 0.35, 3.0, 0.05)
        cam.ox = cam.oy = 100.0
        anchor = (400.0, 300.0)
        before = cam.screen_to_hex(*anchor)
        for _ in range(60):
            cam.zoom_by(1, anchor)
        self.assertEqual(cam.zoom, 3.0)
        for _ in range(120):
            cam.zoom_by(-1, anchor)
        self.assertEqual(cam.zoom, 0.35)
        self.assertAlmostEqual(cam.zoom / 0.05, round(cam.zoom / 0.05), places=6)
        cam.zoom, cam.ox, cam.oy = 1.0, 100.0, 100.0
        cam.zoom_by(2, anchor)
        self.assertEqual(cam.screen_to_hex(*anchor), before)

    def test_fit_centres_the_box(self):
        cam = Camera(20, 0.35, 3.0)
        cam.fit((0, 0, 1000, 500), (0, 0, 800, 600))
        self.assertLessEqual(cam.zoom * 1000, 800)
        self.assertAlmostEqual(cam.ox + 500 * cam.zoom, 400, delta=1)


@unittest.skipUnless(HAVE_PYGAME, "pygame-ce not installed")
class TestScreens(unittest.TestCase):
    def test_shot_renders_both_screens(self):
        from fieldbox.ui.shot import render_screen
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("mapview", "symbols"):
                out = render_screen(name, os.path.join(tmp, f"{name}.png"), seed=1, preset="tiny")
                self.assertGreater(os.path.getsize(out), 5000)

    def test_mapview_keys_and_hover(self):
        import pygame
        from fieldbox.ui.app import App
        from fieldbox.ui.screens.mapview import MapViewScreen
        app = App((1280, 720))
        try:
            scr = MapViewScreen(app, seed=3, preset="tiny")
            key = lambda k: scr.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k))  # noqa: E731
            key(pygame.K_n)
            self.assertEqual(scr.seed, 4)
            key(pygame.K_b)
            key(pygame.K_b)
            self.assertEqual(scr.seed, 2)
            key(pygame.K_SPACE)
            self.assertNotEqual(scr.seed, 2)
            key(pygame.K_2)
            self.assertEqual(scr.preset, "standard")
            key(pygame.K_1)
            self.assertEqual(scr.preset, "tiny")
            key(pygame.K_p)
            key(pygame.K_g)
            self.assertFalse(scr.tint)
            self.assertTrue(scr.grid)
            x, y = scr.camera.hex_to_screen(scr.world.hq[scr.world.hq and next(iter(scr.world.hq))])
            scr.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(int(x), int(y)), rel=(0, 0), buttons=(0, 0, 0)))
            self.assertIsNotNone(scr.hover)
            self.assertTrue(any("structure" in s for s in scr._hover_lines()))
            app.window.fill((0, 0, 0))
            scr.draw(app.window)
        finally:
            pygame.quit()


if __name__ == "__main__":
    unittest.main()
