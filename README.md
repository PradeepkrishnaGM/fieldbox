# Fieldbox

Two AI commanders, BLUE and RED, fight over a randomly generated world
across land, sea and air. Each side runs an economy and infrastructure:
headquarters, industry, power plants, a data center, radar sites, airfields,
ports and forward bases. Think RISK crossed with chess: provinces and income,
discrete simultaneous turns, and every piece with a clear role.

The point is to **watch the AI adapt**. Each commander's doctrine is a set of
traits (aggression, force mix, what to protect, what to target). A population
of doctrines evolves through league play, and commanders counter-build
against what they observe within a match.

**Status: M2** — deterministic headless engine (nine unit types,
logistics, scripted and doctrinal agents, Elo arena, CSV telemetry), plus a
seeded, point-symmetric world generator (terrain, central sea, rivers,
cities, roads, provinces, regions, starting positions) and a desktop
viewer to browse the maps. Maps are not connected to the game engine yet.
Economy, fog of war and evolution are next.

## Quick start

The engine needs only Python 3.10+ and the standard library.

```bash
python -m fieldbox play --watch             # one match, ASCII map, animated
python -m fieldbox play --blue doctrine --red rush --seed 3
python -m fieldbox tournament --games 10    # round robin with Elo
python -m fieldbox bench                    # engine throughput
python -m fieldbox report --games 20        # batch diagnostics and anomalies
```

World generator and viewer (the viewer needs `pygame-ce`; `pip install pygame-ce`,
ideally in a venv):

```bash
python -m fieldbox worldgen --seed 3 --preset standard   # stats only, no GUI
python -m fieldbox gui --screen mapview                  # browse worlds
python -m fieldbox gui --screen symbols                  # unit and structure symbols
python -m fieldbox.ui.shot --screen mapview --seed 2 --out shots/m.png   # headless screenshot
```

Map viewer keys: `N`/`B` next/previous seed, `Space` random seed, `1 2 3`
tiny/standard/large, `P` province tint, `G` hex grid, `WASD` or arrows or
right-drag to pan, wheel to zoom, `Home` to fit, `Esc` to quit.

Tests (the UI tests are skipped when pygame-ce is not installed):

```bash
python -m unittest discover -s tests                   # quick
FIELDBOX_SLOW=1 python -m unittest discover -s tests   # full
```

All tunable numbers live in `fieldbox/params.py`.

## Licence

MIT
