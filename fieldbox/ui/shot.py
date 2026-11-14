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
                  size=UIParams().window) -> str:
    """Render one frame of a screen to a PNG; returns the path."""
    app = App(size)
    try:
        app.push(make_screen(name, app, seed, preset))
        app.draw_frame()
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(app.window, out)
    finally:
        pygame.quit()
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="fieldbox.ui.shot")
    p.add_argument("--screen", default="mapview", choices=["mapview", "symbols"])
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--preset", default="standard")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    print(render_screen(a.screen, a.out, a.seed, a.preset))


if __name__ == "__main__":
    main()
