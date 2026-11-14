"""Main menu (PLAN.md 5.8) and the replay picker."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pygame

from ... import settings
from .. import theme
from ..app import Screen
from ..widgets import Button

REPLAY_DIRS = (".", "replays", "runs/evo")


def find_replays(limit: int = 16) -> List[Path]:
    """Saved replays, newest first: ./*.fbr, ./replays/*.fbr and runs/evo/*/replays/*.fbr."""
    found = []
    for d in REPLAY_DIRS:
        base = Path(d)
        if base.exists():
            found += list(base.glob("*.fbr")) + list(base.glob("*/replays/*.fbr")) + \
                list(base.glob("*/*.fbr"))
    uniq = {p.resolve(): p for p in found}
    return sorted(uniq.values(), key=lambda p: -p.stat().st_mtime)[:limit]


class MenuScreen(Screen):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.buttons: List[Button] = []
        self.settings = settings.load()
        self.note = ""

    # -- actions ----------------------------------------------------------------

    def new_match(self) -> None:
        from .setup import SetupScreen
        self.app.push(SetupScreen(self.app))

    def watch_replay(self) -> None:
        self.app.push(ReplayPicker(self.app))

    def evolab(self) -> None:
        from .evolab import EvoLabScreen
        self.app.push(EvoLabScreen(self.app))

    def worldgen(self) -> None:
        from .mapview import MapViewScreen
        self.app.push(MapViewScreen(self.app, seed=1, preset="standard"))

    def symbols(self) -> None:
        from .symbols_sheet import SymbolsScreen
        self.app.push(SymbolsScreen(self.app))

    def quit(self) -> None:
        self.app.running = False

    def cycle_window(self) -> None:
        sizes = settings.WINDOW_SIZES
        cur = tuple(self.settings["window"])
        nxt = sizes[(sizes.index(cur) + 1) % len(sizes)] if cur in sizes else sizes[0]
        self.settings["window"] = list(nxt)
        self.app.window = pygame.display.set_mode(nxt, pygame.RESIZABLE)
        self._save()

    def cycle_speed(self) -> None:
        sp = settings.ANIM_SPEEDS
        i = sp.index(self.settings["anim_speed"]) if self.settings["anim_speed"] in sp else 1
        self.settings["anim_speed"] = sp[(i + 1) % len(sp)]
        self._save()

    def _save(self) -> None:
        self.note = "" if settings.save(self.settings) else "settings could not be saved"

    # -- events / drawing ------------------------------------------------------------

    def handle_event(self, e) -> None:
        for b in self.buttons:
            if b.handle_event(e):
                return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.app.pop()

    def draw(self, surf: pygame.Surface) -> None:
        W, H = surf.get_size()
        t = theme.font(54, True).render("FIELDBOX", True, theme.ACCENT)
        surf.blit(t, t.get_rect(center=(W // 2, 130)))
        s = theme.font(16).render("two AI commanders, one generated world, doctrine that evolves",
                                  True, theme.DIM)
        surf.blit(s, s.get_rect(center=(W // 2, 180)))
        items = (("New match", self.new_match), ("Watch replay", self.watch_replay),
                 ("Evolution lab", self.evolab), ("World generator", self.worldgen),
                 ("Symbols", self.symbols), ("Quit", self.quit))
        self.buttons = []
        bw, bh, gap = 320, 46, 14
        y = 240
        for label, fn in items:
            self.buttons.append(Button(pygame.Rect((W - bw) // 2, y, bw, bh), label, fn))
            y += bh + gap
        cur = self.settings["window"]
        self.buttons.append(Button(pygame.Rect(W // 2 - 250, H - 70, 240, 30),
                                   f"Window {cur[0]}x{cur[1]}", self.cycle_window))
        self.buttons.append(Button(pygame.Rect(W // 2 + 10, H - 70, 240, 30),
                                   f"Playback speed {self.settings['anim_speed']:g}x", self.cycle_speed))
        for b in self.buttons:
            b.draw(surf)
        if self.note:
            surf.blit(theme.font(12).render(self.note, True, theme.BAD), (W // 2 - 250, H - 32))


class ReplayPicker(Screen):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.files = find_replays()
        self.buttons: List[Button] = []
        self.error = ""

    def open(self, path: Path) -> None:
        from ...record.replay import Replay, ReplayError
        from .match import MatchScreen
        try:
            rep = Replay.load(path)
            self.app.push(MatchScreen(self.app, rep.world(), rep, None))
        except (ReplayError, OSError, ValueError) as e:
            self.error = f"{path.name}: {e}"[:90]

    def handle_event(self, e) -> None:
        for b in self.buttons:
            if b.handle_event(e):
                return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.app.pop()

    def draw(self, surf: pygame.Surface) -> None:
        W, H = surf.get_size()
        surf.blit(theme.font(26, True).render("WATCH REPLAY", True, theme.ACCENT), (40, 30))
        self.buttons = [Button(pygame.Rect(40, H - 60, 120, 34), "Back", self.app.pop)]
        if not self.files:
            for i, line in enumerate(("No replays found.",
                                      "Save one with:  fieldbox-cli play --save match.fbr",
                                      "Evolution runs save one per generation in runs/evo/<run>/replays/.")):
                surf.blit(theme.font(15).render(line, True, theme.DIM if i else theme.TEXT), (40, 90 + 26 * i))
        for i, p in enumerate(self.files):
            self.buttons.append(Button(pygame.Rect(40, 90 + i * 36, min(W - 80, 900), 30), str(p),
                                       lambda p=p: self.open(p)))
        if self.error:
            surf.blit(theme.font(13).render(self.error, True, theme.BAD), (180, H - 52))
        for b in self.buttons:
            b.draw(surf)
