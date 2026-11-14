"""Window, screen stack and main loop."""

from __future__ import annotations

from typing import List, Optional

import pygame

from ..params import UIParams
from . import theme

UI = UIParams()


class Screen:
    """Base class. A screen draws itself and reacts to events; it never owns
    the window."""

    def __init__(self, app: "App") -> None:
        self.app = app

    def on_enter(self) -> None: ...
    def handle_event(self, e: pygame.event.Event) -> None: ...
    def update(self, dt: float) -> None: ...
    def draw(self, surf: pygame.Surface) -> None: ...


class App:
    def __init__(self, size=None) -> None:
        if size is None:
            from .. import settings
            size = tuple(settings.load()["window"])
        pygame.init()
        theme.reset_fonts()
        self.window = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption("Fieldbox")
        self.clock = pygame.time.Clock()
        self.stack: List[Screen] = []
        self.running = True

    def push(self, screen: Screen) -> None:
        self.stack.append(screen)
        screen.on_enter()

    def pop(self) -> None:
        if self.stack:
            self.stack.pop()
        if not self.stack:
            self.running = False

    @property
    def top(self) -> Optional[Screen]:
        return self.stack[-1] if self.stack else None

    def draw_frame(self) -> None:
        self.window.fill(theme.BG)
        if self.top:
            self.top.draw(self.window)

    def run(self) -> None:
        while self.running and self.top:
            dt = self.clock.tick(UI.fps) / 1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.running = False
                elif e.type == pygame.VIDEORESIZE:
                    w, h = max(e.w, UI.min_window[0]), max(e.h, UI.min_window[1])
                    if (w, h) != (e.w, e.h):
                        self.window = pygame.display.set_mode((w, h), pygame.RESIZABLE)
                else:
                    self.top.handle_event(e)
            self.top.update(dt)
            self.draw_frame()
            pygame.display.flip()
        pygame.quit()


def make_screen(name: str, app: App, seed: int = 1, preset: str = "standard", **opts) -> Screen:
    if name == "mapview":
        from .screens.mapview import MapViewScreen
        return MapViewScreen(app, seed=seed, preset=preset)
    if name == "symbols":
        from .screens.symbols_sheet import SymbolsScreen
        return SymbolsScreen(app)
    if name == "match":
        from .screens.match import MatchScreen
        return MatchScreen.create(app, seed=seed, preset=preset, **opts)
    if name == "menu":
        from .screens.menu import MenuScreen
        return MenuScreen(app)
    if name == "setup":
        from .screens.setup import SetupScreen
        return SetupScreen(app, root=opts.get("root") or "runs/evo")
    if name == "evolab":
        from .screens.evolab import EvoLabScreen
        return EvoLabScreen(app, root=opts.get("root") or "runs/evo", run=opts.get("run"))
    raise ValueError(f"unknown screen '{name}'")


def run_gui(name: str, seed: int = 1, preset: str = "standard", **opts) -> None:
    app = App()
    app.push(make_screen(name, app, seed, preset, **opts))
    app.run()
