"""Render the README figures from an evolution run (needs pygame-ce).

    python scripts/make_figures.py runs/evo/main docs
Writes docs/evo_traits.png, docs/evo_elo.png and docs/evo_xgen.png.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402

from fieldbox import params as P  # noqa: E402
from fieldbox.ui import charts, theme  # noqa: E402

TRAIT_PAGES = (("aggression", "concentration", "expansion", "adaptivity", "air_role", "retreat"),
               ("w_build_infantry", "w_build_tank", "w_build_artillery", "w_build_sam",
                "w_build_helicopter", "w_build_jet"))


def main(run_dir: str, out_dir: str) -> None:
    run = Path(run_dir)
    gens = [json.loads(f.read_text()) for f in sorted(run.glob("gen_[0-9][0-9][0-9].json"))]
    xgen = json.loads((run / "xgen.json").read_text()) if (run / "xgen.json").exists() else None
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pygame.init()
    theme.reset_fonts()

    def save(name, w, h, draw):
        s = pygame.Surface((w, h))
        s.fill(theme.BG)
        draw(s, pygame.Rect(0, 0, w, h))
        pygame.image.save(s, str(out / name))

    series = {t.replace("w_build_", "build "): [g["trait_stats"][t][0] for g in gens]
              for t in TRAIT_PAGES[0] + TRAIT_PAGES[1][:0]}
    save("evo_traits.png", 900, 380, lambda s, r: charts.line_chart(
        s, r, "Trait means per generation (population average)", series, 0.0, 1.0))
    elo = {"champion": [g["elo"]["rating"] for g in gens]}
    for a in P.EVO_ANCHORS:
        elo[a.replace("doctrine-", "")] = [P.EVO_ANCHOR_ELO[a]] * len(gens)
    lo, hi = min(min(v) for v in elo.values()) - 50, max(max(v) for v in elo.values()) + 50
    save("evo_elo.png", 900, 380, lambda s, r: charts.line_chart(
        s, r, "Champion Elo vs fixed anchors", elo, lo, hi, "{:.0f}",
        dashed=[a.replace("doctrine-", "") for a in P.EVO_ANCHORS]))
    if xgen:
        save("evo_xgen.png", 520, 520, lambda s, r: charts.heatmap(
            s, r, "Cross-generation win rate (row beats column)", xgen["matrix"],
            [f"g{g}" for g in xgen["gens"]]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
