"""Debug screen: every unit counter and structure icon, for review."""

from __future__ import annotations

import pygame

from ...sim.units import Side
from .. import theme
from ..app import Screen
from ..symbols import STRUCTURE_KINDS, UNIT_KINDS, draw_counter, draw_structure
from ..widgets import Label


class SymbolsScreen(Screen):
    S = 44          # hex size used for the sheet, so symbols are big enough to judge

    def handle_event(self, e) -> None:
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.app.pop()

    def draw(self, surf) -> None:
        Label("SYMBOL SHEET  (Esc to quit)", 20, theme.ACCENT, True).draw(surf, (30, 20))
        cols = ("BLUE", "RED", "BLUE 60%", "RED 25%, cut off", "ghost age 2")
        x0, y0, dx, dy = 230, 90, 120, 78
        for i, c in enumerate(cols):
            Label(c, 14, theme.DIM).draw(surf, (x0 + i * dx - 30, y0 - 34))
        for row, kind in enumerate(UNIT_KINDS):
            y = y0 + row * dy
            Label(kind, 15).draw(surf, (30, y - 8))
            draw_counter(surf, (x0, y), self.S, Side.BLUE, kind)
            draw_counter(surf, (x0 + dx, y), self.S, Side.RED, kind)
            draw_counter(surf, (x0 + 2 * dx, y), self.S, Side.BLUE, kind, strength=60)
            draw_counter(surf, (x0 + 3 * dx, y), self.S, Side.RED, kind, strength=25, out_of_supply=True)
            draw_counter(surf, (x0 + 4 * dx, y), self.S, Side.BLUE, kind, ghost_age=2)
        sx = x0 + 6 * dx
        Label("STRUCTURES  (BLUE, RED at 70%)", 14, theme.DIM).draw(surf, (sx - 30, y0 - 52))
        for row, kind in enumerate(STRUCTURE_KINDS):
            y = y0 + row * dy
            Label(kind, 15).draw(surf, (sx + 2 * dx - 60, y - 8))
            draw_structure(surf, (sx, y), self.S, Side.BLUE, kind)
            draw_structure(surf, (sx + dx, y), self.S, Side.RED, kind, strength=70, ring=True)
