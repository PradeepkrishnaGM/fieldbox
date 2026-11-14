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

**Status: M1** — deterministic headless engine with nine unit types,
logistics, scripted and doctrinal agents, an Elo arena and CSV telemetry.
The generated world, economy, fog of war, evolution and the desktop viewer
are next.

## Quick start

The engine needs only Python 3.10+ and the standard library.

```bash
python -m fieldbox play --watch             # one match, ASCII map, animated
python -m fieldbox play --blue doctrine --red rush --seed 3
python -m fieldbox tournament --games 10    # round robin with Elo
python -m fieldbox bench                    # engine throughput
python -m fieldbox report --games 20        # batch diagnostics and anomalies
```

Tests:

```bash
python -m unittest discover -s tests                   # quick
FIELDBOX_SLOW=1 python -m unittest discover -s tests   # full
```

All tunable numbers live in `fieldbox/params.py`.

## Licence

MIT
