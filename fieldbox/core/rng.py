"""Seed derivation. Every random stream in Fieldbox comes from here.

``derive_seed(*parts)`` turns a tuple of named parts (for example
``("worldgen", map_seed, "rivers")``) into a stable 64-bit seed. It uses
sha256 over ``repr(parts)``, so it is identical across processes and
machines -- unlike ``hash()``, which Python salts per process.
"""

from __future__ import annotations

import hashlib
import random


def derive_seed(*parts) -> int:
    """First 8 bytes of sha256(repr(parts)), as an unsigned int."""
    digest = hashlib.sha256(repr(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def make_rng(*parts) -> random.Random:
    """A fresh ``random.Random`` seeded from ``derive_seed(*parts)``."""
    return random.Random(derive_seed(*parts))
