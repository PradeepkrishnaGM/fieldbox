"""Turn a replay into a short GIF (needs pygame-ce and ffmpeg).

    python scripts/make_gif.py match.fbr docs/demo.gif [--step 2] [--fps 6] [--width 800]
Renders every `step`-th turn with the match viewer, then lets ffmpeg build a palette GIF.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402

from fieldbox.record.replay import Replay  # noqa: E402
from fieldbox.ui.app import App  # noqa: E402
from fieldbox.ui.screens.match import MatchScreen  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("replay")
    ap.add_argument("out")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--fps", type=int, default=6)
    ap.add_argument("--width", type=int, default=800)
    a = ap.parse_args()
    rep = Replay.load(a.replay)
    app = App((1600, 900))
    scr = MatchScreen(app, rep.world(), rep, None)
    app.push(scr)
    scr.update(0.0)
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for k in list(range(0, scr.last + 1, a.step)) + [scr.last]:
            scr.goto(k)
            app.draw_frame()
            pygame.image.save(app.window, os.path.join(tmp, f"f{n:04d}.png"))
            n += 1
        pygame.quit()
        vf = f"fps={a.fps},scale={a.width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps),
                        "-i", os.path.join(tmp, "f%04d.png"), "-vf", vf, a.out], check=True)
    print(a.out, os.path.getsize(a.out) // 1024, "KB,", n, "frames")


if __name__ == "__main__":
    sys.exit(main())
