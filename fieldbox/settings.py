"""User settings in ~/.config/fieldbox/settings.json (stdlib only; optional).

The app works without the file. Set FIELDBOX_CONFIG to use another path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from . import params as P

WINDOW_SIZES = ((1280, 720), (1600, 900), (1920, 1080))
ANIM_SPEEDS = (0.5, 1.0, 2.0, 4.0)       # match viewer playback speed (index into the viewer's list)
DEFAULTS: Dict[str, Any] = {"window": list(P.UIParams().window), "anim_speed": 1.0}


def path() -> Path:
    env = os.environ.get("FIELDBOX_CONFIG")
    return Path(env) if env else Path.home() / ".config" / "fieldbox" / "settings.json"


def load() -> Dict[str, Any]:
    """Settings merged over the defaults; a missing or broken file gives the defaults."""
    out = dict(DEFAULTS)
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(d, dict):
        w = d.get("window")
        if isinstance(w, list) and len(w) == 2 and all(isinstance(x, int) for x in w):
            out["window"] = [max(w[0], P.UIParams().min_window[0]), max(w[1], P.UIParams().min_window[1])]
        if d.get("anim_speed") in ANIM_SPEEDS:
            out["anim_speed"] = d["anim_speed"]
    return out


def save(s: Dict[str, Any]) -> bool:
    """Write settings; returns False (never raises) if the location is not writable."""
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except OSError:
        return False
