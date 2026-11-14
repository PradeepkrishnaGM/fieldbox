"""Events: the engine's record of what happened (PLAN.md §3.5).

Every phase appends events. They drive the replay, the viewer's animation and
the determinism checks: equal seeds and agents must give an identical event
stream, so `stream_hash` is the single fingerprint of a match.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Kinds used in M3. Later milestones add structure, build and fog kinds.
MOVED = "moved"
ENGAGED = "engaged"
UNIT_DESTROYED = "unit_destroyed"
PROVINCE_FLIPPED = "province_flipped"
ORDER_REJECTED = "order_rejected"
SUPPLY_CUT = "supply_cut"
GAME_OVER = "game_over"
TURN_END = "turn_end"


@dataclass(frozen=True)
class Event:
    turn: int
    kind: str
    side: Optional[int] = None        # Side value, when the event belongs to one side
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"t": self.turn, "k": self.kind, "s": self.side, "d": self.data},
                          sort_keys=True, separators=(",", ":"))


def stream_hash(events: Iterable[Event]) -> str:
    h = hashlib.sha256()
    for e in events:
        h.update(e.to_json().encode())
        h.update(b"\n")
    return h.hexdigest()
