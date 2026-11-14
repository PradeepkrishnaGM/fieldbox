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

**Status: M5** — a match viewer: watch a game live in the window (agents
step turn by turn, units glide along their paths, engagements flash) or open
a saved replay, with a selection card, event log, timeline scrubber and an end
overlay. Underneath is the M4 engine: structures, power, production,
command points, FOBs, structure-based supply and HQ victory, on generated
point-symmetric worlds. Fog of war and evolution are next.

## Quick start

The engine needs only Python 3.10+ and the standard library.

```bash
python -m fieldbox play --watch             # one match, ASCII map, animated
python -m fieldbox play --blue doctrine --red rush --seed 3 --save runs/replays/a.fbr
python -m fieldbox tournament --games 10 --preset tiny   # round robin with Elo (tiny|standard|large)
python -m fieldbox bench                    # engine throughput
python -m fieldbox report --games 20        # batch diagnostics and anomalies
```

World generator and viewer (the viewer needs `pygame-ce`; `pip install pygame-ce`,
ideally in a venv):

```bash
python -m fieldbox worldgen --seed 3 --preset standard   # stats only, no GUI
python -m fieldbox gui --screen mapview                  # browse worlds
python -m fieldbox gui --screen symbols                  # unit and structure symbols
python -m fieldbox gui --screen match --blue doctrine --red rush --seed 1 --preset standard   # watch live
python -m fieldbox gui --screen match --replay runs/replays/a.fbr                              # watch a replay
python -m fieldbox.ui.shot --screen mapview --seed 2 --out shots/m.png   # headless screenshot
```

Map viewer keys: `N`/`B` next/previous seed, `Space` random seed, `1 2 3`
tiny/standard/large, `P` province tint, `G` hex grid, `WASD` or arrows or
right-drag to pan, wheel to zoom, `Home` to fit, `Esc` to quit.

Match viewer keys: `Space` play/pause, `Left`/`Right` step, `Home`/`End` first/last
turn, `+`/`-` speed, click a unit or structure to inspect it, drag the timeline to
scrub, mouse wheel over the right panel scrolls the log, `F` fit, `G` grid,
`P` province tint, `O` or `Enter` toggles the end overlay.

Tests (the UI tests are skipped when pygame-ce is not installed):

```bash
python -m unittest discover -s tests                   # quick
FIELDBOX_SLOW=1 python -m unittest discover -s tests   # full
```

All tunable numbers live in `fieldbox/params.py`.

## Licence

MIT
