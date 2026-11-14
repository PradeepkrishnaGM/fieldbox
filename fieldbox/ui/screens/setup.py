"""Match setup (PLAN.md 5.8): map, seeds, turn limit and both doctrines, with a map preview."""

from __future__ import annotations

import random
from typing import Callable, List, Optional

import pygame

from ... import params as P
from ...league import roster
from ...world import generate_world
from .. import theme
from ..app import Screen
from ..camera import Camera
from ..hexdraw import MapRenderer
from ..widgets import Button

UI = P.UIParams()
PRESET_NAMES = ("tiny", "standard", "large")
ROOT = "runs/evo"


class SetupScreen(Screen):
    def __init__(self, app, root: str = ROOT) -> None:
        super().__init__(app)
        self.root = root
        self.preset = "tiny"
        self.map_seed = 1
        self.turns: Optional[int] = None            # None = the preset's turn limit
        self.game_seed = 0
        self.names = roster.names(root)
        self.blue = self.names.index("doctrine")
        self.red = self.names.index("rush")
        self._preview_key = None
        self._renderer: Optional[MapRenderer] = None
        self._world = None
        self._buttons: List[Button] = []
        self.error = ""

    # -- state ------------------------------------------------------------------

    def preset_turns(self) -> int:
        return P.PRESETS[self.preset].match.max_turns

    def world(self):
        key = (self.preset, self.map_seed)
        if key != self._preview_key:
            self._world = generate_world(self.map_seed, self.preset)
            self._renderer = MapRenderer(self._world, UI.hex_size)
            self._preview_key = key
        return self._world

    def cycle_preset(self) -> None:
        self.preset = PRESET_NAMES[(PRESET_NAMES.index(self.preset) + 1) % len(PRESET_NAMES)]
        self.turns = None

    def add_seed(self, d: int) -> None:
        self.map_seed = max(0, self.map_seed + d)

    def add_game_seed(self, d: int) -> None:
        self.game_seed = max(0, self.game_seed + d)

    def add_turns(self, d: int) -> None:
        t = (self.turns or self.preset_turns()) + 5 * d
        self.turns = max(5, t)

    def random_seed(self) -> None:
        self.map_seed = random.randrange(1, 10_000)

    def pick(self, side: str, d: int) -> None:
        n = len(self.names)
        setattr(self, side, (getattr(self, side) + d) % n)

    def start(self) -> None:
        from .match import MatchScreen
        try:
            scr = MatchScreen.create(self.app, seed=self.map_seed, preset=self.preset,
                                     blue=self.names[self.blue], red=self.names[self.red],
                                     game_seed=self.game_seed, turns=self.turns, root=self.root)
        except SystemExit as e:
            self.error = str(e)
            return
        self.app.push(scr)

    # -- events -------------------------------------------------------------

    def handle_event(self, e) -> None:
        for b in self._buttons:
            if b.handle_event(e):
                return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.app.pop()
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_RETURN:
            self.start()

    # -- drawing ------------------------------------------------------------------

    def _row(self, surf, y: int, label: str, value: str, minus: Callable, plus: Callable,
             extra: Optional[tuple] = None) -> None:
        surf.blit(theme.font(15).render(label, True, theme.DIM), (40, y + 4))
        surf.blit(theme.font(16).render(value, True, theme.TEXT), (220, y + 4))
        self._buttons.append(Button(pygame.Rect(400, y, 34, 28), "-", minus))
        self._buttons.append(Button(pygame.Rect(440, y, 34, 28), "+", plus))
        if extra:
            self._buttons.append(Button(pygame.Rect(482, y, 90, 28), extra[0], extra[1]))

    def draw(self, surf: pygame.Surface) -> None:
        W, H = surf.get_size()
        self._buttons = []
        surf.blit(theme.font(26, True).render("NEW MATCH", True, theme.ACCENT), (40, 30))
        y = 90
        step = 44
        surf.blit(theme.font(15).render("map size", True, theme.DIM), (40, y + 4))
        surf.blit(theme.font(16).render(self.preset, True, theme.TEXT), (220, y + 4))
        self._buttons.append(Button(pygame.Rect(400, y, 74, 28), "next", self.cycle_preset))
        y += step
        self._row(surf, y, "map seed", str(self.map_seed), lambda: self.add_seed(-1),
                  lambda: self.add_seed(1), ("random", self.random_seed))
        y += step
        self._row(surf, y, "max turns", str(self.turns or self.preset_turns()) +
                  ("" if self.turns else " (preset)"), lambda: self.add_turns(-1), lambda: self.add_turns(1))
        y += step
        self._row(surf, y, "game seed", str(self.game_seed), lambda: self.add_game_seed(-1),
                  lambda: self.add_game_seed(1))
        y += step + 10
        for side, label, col in (("blue", "BLUE doctrine", theme.SIDE_LIGHT[theme.Side.BLUE]),
                                 ("red", "RED doctrine", theme.SIDE_LIGHT[theme.Side.RED])):
            surf.blit(theme.font(15).render(label, True, col), (40, y + 4))
            surf.blit(theme.font(15).render(self.names[getattr(self, side)], True, theme.TEXT), (220, y + 4))
            self._buttons.append(Button(pygame.Rect(400, y, 34, 28), "<", lambda s=side: self.pick(s, -1)))
            self._buttons.append(Button(pygame.Rect(440, y, 34, 28), ">", lambda s=side: self.pick(s, 1)))
            y += step
        y += 16
        self._buttons.append(Button(pygame.Rect(40, y, 180, 38), "Start match", self.start))
        self._buttons.append(Button(pygame.Rect(232, y, 120, 38), "Back", self.app.pop))
        if self.error:
            surf.blit(theme.font(13).render(self.error[:70], True, theme.BAD), (40, y + 48))
        n_ch = len(self.names) - len(roster.BUILTIN)
        note = f"{n_ch} saved champion(s) from {self.root}" if n_ch else \
            f"no saved champions yet (run `fieldbox-cli evolve`, then they appear here)"
        surf.blit(theme.font(12).render(note, True, theme.DIM), (40, H - 36))

        # preview
        pr = pygame.Rect(620, 90, W - 660, H - 180)
        pygame.draw.rect(surf, theme.PANEL, pr)
        pygame.draw.rect(surf, theme.PANEL_BORDER, pr, 1)
        w = self.world()
        cam = Camera(UI.hex_size, 0.1, UI.zoom_max, UI.zoom_step)
        cam.fit(self._renderer.bbox, (pr.x + 6, pr.y + 6, pr.w - 12, pr.h - 12))
        surf.set_clip(pr.inflate(-2, -2))
        layer, wx0, wy0 = self._renderer.layer(cam.zoom, True, False)
        surf.blit(layer, (wx0 * cam.zoom + cam.ox, wy0 * cam.zoom + cam.oy))
        surf.set_clip(None)
        surf.blit(theme.font(13).render(
            f"preview: {self.preset} seed {self.map_seed}   {len(w.provinces)} provinces", True, theme.DIM),
            (pr.x, pr.y - 20))
        for b in self._buttons:
            b.draw(surf)
