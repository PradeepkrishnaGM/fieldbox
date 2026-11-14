"""Terminal rendering and state export.

The ASCII view is for debugging; `to_json` is the wire format the MapLibre +
milsymbol frontend will consume in Phase 3, so the engine never imports a
rendering library.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..core.hexgrid import TERRAIN_CHAR, Domain
from ..sim.units import Side

if TYPE_CHECKING:
    from ..sim.engine import Game

ANSI = {
    Side.BLUE: "\033[94m",
    Side.RED: "\033[91m",
}
RESET = "\033[0m"
OBJ_COLOR = "\033[93m"


CELL_W = 3  # every cell is exactly this wide, or the hex rows go ragged

# Which domain "owns" the cell when several units share a hex.
_STACK_ORDER = (Domain.LAND, Domain.SEA, Domain.AIR)


def _strength_pips(strength: float) -> str:
    """Strength as tenths, right-aligned in 2 chars.

    A living unit never shows 0: at 4% strength `int(4) // 10` was 0, which
    read as 'dead' on the map. Anything still alive shows at least 1.
    """
    pips = int(round(strength / 10.0))
    if strength > 0:
        pips = max(1, pips)
    return f"{pips:>2}"


def _supply_pips(unit) -> str:
    """Strength, with the leading pad replaced by '!' when out of supply."""
    if unit.in_supply:
        return _strength_pips(unit.strength)
    pips = max(1, min(9, int(round(unit.strength / 10.0))))
    return f"!{pips}"


def _stack_cell(units) -> str:
    """Render one hex's worth of units in exactly CELL_W characters.

    Stacking is per domain, so a hex can hold a tank, a frigate and a
    helicopter at once. With three characters to play with, a single unit
    shows its strength and a stack shows its composition instead -- knowing
    that a helicopter is loitering over that tank matters more than knowing
    the tank is at 70%.
    """
    ordered = sorted(units, key=lambda u: _STACK_ORDER.index(u.domain))
    primary = ordered[0]
    glyph = primary.stats.glyph
    if primary.side is Side.RED:
        glyph = glyph.lower()
    if len(ordered) == 1:
        return f"{glyph}{_supply_pips(primary)}"
    if len(ordered) == 2:
        other = ordered[1].stats.glyph
        if ordered[1].side is Side.RED:
            other = other.lower()
        return f"{glyph}+{other}"
    return f"{glyph}+{len(ordered)}"


def render(game: "Game", color: bool = True) -> str:
    lines = []
    header = (
        f"Turn {game.turn}/{game.max_turns}   "
        f"BLUE {game.vp[Side.BLUE]:.0f} VP  |  RED {game.vp[Side.RED]:.0f} VP"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for row in range(game.map.height):
        offset = row // 2
        indent = "  " * (row % 2)
        cells = []
        for col in range(game.map.width):
            coord = (col - offset, row)
            stack = game.units_at(coord)
            if stack:
                cell = _stack_cell(stack)
                if color:
                    lead = sorted(stack, key=lambda u: _STACK_ORDER.index(u.domain))[0]
                    cell = f"{ANSI[lead.side]}{cell}{RESET}"
            elif coord in game.objectives:
                cell = f"O{game.objectives[coord]} "
                if color:
                    cell = f"{OBJ_COLOR}{cell}{RESET}"
            else:
                road = "=" if coord in game.map.roads else " "
                cell = f"{TERRAIN_CHAR[game.map.get(coord)]}{road} "
            cells.append(cell)
        lines.append(indent + " ".join(cells))

    lines.append("")
    lines.append("land I=INF A=TNK M=IFV R=RCN T=ART S=SAM E=ENG   air H=HEL J=JET   sea N=SHP")
    lines.append("uppercase=BLUE  lowercase=RED   number = strength in tenths (10 = full)")
    lines.append("X+Y = two units stacked (one per domain), X+n = n units in the hex")
    lines.append("X!n = OUT OF SUPPLY -- stock draining, combat power falling")
    lines.append(". plain  f forest  ^ hill  % mountain  # urban  ~ water  = road  On capital (value n)")
    return "\n".join(lines)


def to_json(game: "Game") -> str:
    """Full state dump, including SIDC codes ready for milsymbol."""
    payload = {
        "turn": game.turn,
        "max_turns": game.max_turns,
        "vp": {s.name: v for s, v in game.vp.items()},
        "map": {
            "width": game.map.width,
            "height": game.map.height,
            "terrain": [
                {"q": q, "r": r, "t": int(t)} for (q, r), t in game.map.terrain.items()
            ],
            "roads": [{"q": q, "r": r} for q, r in game.map.roads],
        },
        "objectives": [
            {
                "q": q, "r": r, "value": v,
                "holder": (h.name if (h := game.objective_holder((q, r))) is not None else None),
            }
            for (q, r), v in game.objectives.items()
        ],
        "units": [
            {
                "uid": u.uid,
                "side": u.side.name,
                "type": u.stats.label,
                "domain": u.domain.name,
                "q": u.q,
                "r": u.r,
                "strength": round(u.strength, 1),
                "entrench": u.entrench,
                "sidc": u.sidc(),
            }
            for u in game.units.values()
            if u.alive
        ],
    }
    return json.dumps(payload)
