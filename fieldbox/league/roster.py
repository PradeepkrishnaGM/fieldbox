"""Doctrines a player can pick: the built-in agents plus saved evolution champions.

Champion names look like `champion:<run>:<gen>`; the genome is read from
`<root>/<run>/gen_<gen>.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..agents import REGISTRY, Commander, DoctrineGenome

BUILTIN = ("doctrine", "rush", "doctrine-legacy", "hold", "random")


def names(root: str = "runs/evo") -> List[str]:
    out = [n for n in BUILTIN if n in REGISTRY]
    r = Path(root)
    if r.exists():
        for run in sorted(d for d in r.iterdir() if (d / "meta.json").exists()):
            for f in sorted(run.glob("gen_[0-9][0-9][0-9].json")):
                out.append(f"champion:{run.name}:{int(f.stem[4:])}")
    return out


def make(name: str, root: str = "runs/evo"):
    """A fresh agent for a roster name. Raises SystemExit for unknown names."""
    if name in REGISTRY:
        return REGISTRY[name]()
    kind, _, rest = name.partition(":")
    if kind == "champion":
        run, _, gen = rest.rpartition(":")
        try:
            d = json.loads((Path(root) / run / f"gen_{int(gen):03d}.json").read_text(encoding="utf-8"))
            return Commander(DoctrineGenome.from_dict(d["champion_genome"]), label=name)
        except (OSError, ValueError, KeyError):
            pass
    raise SystemExit(f"unknown agent '{name}'. available: {', '.join(REGISTRY)}, champion:<run>:<gen>")
