"""DoctrineGenome: the evolvable traits of a Commander (PLAN.md 4.3).

Every trait is a float in [0, 1]. Decoders below turn them into engine
quantities; ranges live in `params.GENOME`. Genomes are immutable values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Tuple

from .. import params as P
from ..sim.units import UnitType

BUILD_TRAITS = tuple(f"w_build_{t.name.lower()}" for t in UnitType)
TRAITS: Tuple[str, ...] = (
    "aggression", "concentration", "reserve", "retreat", "expansion",
    *BUILD_TRAITS,
    "econ_invest",
    "defend_hq", "defend_industry", "defend_power", "defend_datacenter",
    "target_units", "target_industry", "target_power", "target_datacenter",
    "target_hq", "target_airfield",
    "air_role", "fob_forward", "adaptivity",
)


def _lerp(rng: Tuple[float, float], t: float) -> float:
    return rng[0] + (rng[1] - rng[0]) * t


def _clip(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


@dataclass(frozen=True)
class DoctrineGenome:
    values: Tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(TRAITS):
            raise ValueError(f"genome needs {len(TRAITS)} traits, got {len(self.values)}")

    # -- construction -----------------------------------------------------

    @classmethod
    def default(cls) -> "DoctrineGenome":
        return cls.from_dict({})

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> "DoctrineGenome":
        """Unlisted traits take the default value (0.5 if not in GENOME_DEFAULTS)."""
        unknown = set(d) - set(TRAITS)
        if unknown:
            raise KeyError(f"unknown traits: {sorted(unknown)}")
        return cls(tuple(_clip(float(d.get(t, P.GENOME_DEFAULTS.get(t, 0.5)))) for t in TRAITS))

    def with_(self, **kw: float) -> "DoctrineGenome":
        d = self.to_dict()
        d.update(kw)
        return DoctrineGenome.from_dict(d)

    def to_dict(self) -> Dict[str, float]:
        return dict(zip(TRAITS, self.values))

    def __getitem__(self, name: str) -> float:
        return self.values[TRAITS.index(name)]

    # -- json (floats round-trip exactly through repr) ---------------------

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "DoctrineGenome":
        return cls.from_dict(json.loads(s))

    # -- evolution operators (rng = random.Random) --------------------------

    def mutate(self, rng, mut_prob: float = None, sigma: float = None) -> "DoctrineGenome":
        mp = P.GENOME.mut_prob if mut_prob is None else mut_prob
        sg = P.GENOME.mut_sigma if sigma is None else sigma
        out = []
        for v in self.values:                 # fixed trait order -> deterministic
            if rng.random() < mp:
                v = _clip(v + rng.gauss(0.0, sg))
            out.append(v)
        return DoctrineGenome(tuple(out))

    @staticmethod
    def crossover(a: "DoctrineGenome", b: "DoctrineGenome", rng,
                  cx_prob: float = None) -> "DoctrineGenome":
        cp = P.GENOME.cx_prob if cx_prob is None else cx_prob
        if rng.random() >= cp:
            return a
        return DoctrineGenome(tuple(x if rng.random() < 0.5 else y
                                    for x, y in zip(a.values, b.values)))

    # -- decoders -----------------------------------------------------------

    @property
    def ratio_mult(self) -> float:
        """Multiplier on the required force ratio (1.6 cautious .. 0.6 bold)."""
        return _lerp(P.GENOME.required_ratio_mult, self["aggression"])

    @property
    def concentration(self) -> float:
        return _lerp(P.GENOME.concentration, self["concentration"])

    @property
    def reserve(self) -> float:
        return _lerp(P.GENOME.reserve, self["reserve"])

    @property
    def withdraw_below(self) -> float:
        return _lerp(P.GENOME.retreat, self["retreat"])

    def build_weights(self) -> Dict[UnitType, float]:
        return {t: self[f"w_build_{t.name.lower()}"] for t in UnitType}
