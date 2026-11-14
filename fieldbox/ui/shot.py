"""Headless screenshots (PLAN.md §5.9).

    python -m fieldbox.ui.shot --screen mapview --seed 1 --preset standard \
        --out shots/mapview_s1.png

Uses SDL's dummy video driver, so it needs no display.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

import pygame  # noqa: E402

from ..params import UIParams  # noqa: E402
from .app import App, make_screen  # noqa: E402


def render_screen(name: str, out: str, seed: int = 1, preset: str = "standard",
                  size=UIParams().window, turns: int = 0, anim=None, select=None,
                  **opts) -> str:
    """Render one frame of a screen to a PNG; returns the path. For the match
    screen, `turns` advances the game first (clamped to its length), `anim`
    freezes mid-animation (0..1) and `select` is ("unit"|"structure", id)."""
    app = App(size)
    try:
        screen = make_screen(name, app, seed, preset, **opts)
        app.push(screen)
        if name == "match":
            screen.update(0.0)                  # lay out the buttons
            screen.advance_to(turns, anim)
            screen.select = select
        app.draw_frame()
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(app.window, out)
    finally:
        pygame.quit()
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="fieldbox.ui.shot")
    p.add_argument("--screen", default="mapview", choices=["mapview", "symbols", "match"])
    p.add_argument("--seed", type=int, default=1, help="map seed")
    p.add_argument("--preset", default="standard")
    p.add_argument("--out", required=True)
    p.add_argument("--turns", type=int, default=0, help="match: advance this many turns first")
    p.add_argument("--anim", type=float, default=None, help="match: freeze mid-turn at 0..1")
    p.add_argument("--blue", default="doctrine")
    p.add_argument("--red", default="rush")
    p.add_argument("--game-seed", type=int, default=0)
    p.add_argument("--replay", default=None, help="match: render a saved replay")
    a = p.parse_args(argv)
    print(render_screen(a.screen, a.out, a.seed, a.preset, turns=a.turns, anim=a.anim,
                        blue=a.blue, red=a.red, game_seed=a.game_seed, replay=a.replay))


if __name__ == "__main__":
    main()
