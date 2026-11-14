"""World generator viewer: browse seeds, presets and overlays."""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

import pygame

from ...core.hexgrid import Terrain
from ...params import PRESETS, UIParams
from ...sim.units import Side
from ...world import generate_world
from .. import theme
from ..app import Screen
from ..camera import Camera
from ..hexdraw import MapRenderer, hex_corners
from ..symbols import STRUCTURE_KINDS, draw_structure
from ..widgets import Label, Panel

UI = UIParams()
PRESET_KEYS = {pygame.K_1: "tiny", pygame.K_2: "standard", pygame.K_3: "large"}
PAN_SPEED = 700.0           # pixels per second while a pan key is held
LEGEND = {"HQ": "HQ", "INDUSTRY": "Industry", "POWER": "Power", "DATACENTER": "Data",
          "RADAR": "Radar", "AIRFIELD": "Airfield", "PORT": "Port", "FOB": "FOB"}
HELP = ["N / B   next / previous seed", "Space   random seed", "1 2 3   tiny / standard / large",
        "P   province tint     G   hex grid", "WASD / arrows / right-drag   pan",
        "wheel   zoom     Home   fit map", "Esc   quit"]


class MapViewScreen(Screen):
    def __init__(self, app, seed: int = 1, preset: str = "standard") -> None:
        super().__init__(app)
        self.seed, self.preset = seed, preset
        self.tint, self.grid = True, False
        self.hover: Optional[Tuple[int, int]] = None
        self.camera = Camera(UI.hex_size, UI.zoom_min, UI.zoom_max, UI.zoom_step)
        self._drag = False
        self.world = None
        self.renderer = None
        self.regenerate()

    # -- state ------------------------------------------------------------

    def map_rect(self) -> pygame.Rect:
        w, h = self.app.window.get_size()
        return pygame.Rect(0, 0, w - UI.panel_width, h)

    def regenerate(self) -> None:
        self.world = generate_world(self.seed, self.preset)
        self.renderer = MapRenderer(self.world, UI.hex_size)
        self.fit()

    def fit(self) -> None:
        r = self.map_rect()
        self.camera.fit(self.renderer.bbox, (r.x, r.y, r.w, r.h))

    # -- events -----------------------------------------------------------

    def handle_event(self, e) -> None:
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.app.pop()
            elif e.key == pygame.K_n:
                self.seed += 1
                self.regenerate()
            elif e.key == pygame.K_b:
                self.seed = max(0, self.seed - 1)
                self.regenerate()
            elif e.key == pygame.K_SPACE:
                self.seed = random.SystemRandom().randrange(1_000_000)
                self.regenerate()
            elif e.key in PRESET_KEYS and PRESET_KEYS[e.key] != self.preset:
                self.preset = PRESET_KEYS[e.key]
                self.regenerate()
            elif e.key == pygame.K_p:
                self.tint = not self.tint
            elif e.key == pygame.K_g:
                self.grid = not self.grid
            elif e.key == pygame.K_HOME:
                self.fit()
        elif e.type == pygame.MOUSEWHEEL:
            pos = pygame.mouse.get_pos()
            if self.map_rect().collidepoint(pos):
                self.camera.zoom_by(e.y, pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button in (2, 3):
            self._drag = True
        elif e.type == pygame.MOUSEBUTTONUP and e.button in (2, 3):
            self._drag = False
        elif e.type == pygame.MOUSEMOTION:
            if self._drag:
                self.camera.pan(*e.rel)
            self._set_hover(e.pos)

    def _set_hover(self, pos) -> None:
        self.hover = None
        if self.map_rect().collidepoint(pos):
            c = self.camera.screen_to_hex(*pos)
            if self.world.hexmap.in_bounds(c):
                self.hover = c

    def update(self, dt: float) -> None:
        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_a] or keys[pygame.K_LEFT]) - (keys[pygame.K_d] or keys[pygame.K_RIGHT])
        dy = (keys[pygame.K_w] or keys[pygame.K_UP]) - (keys[pygame.K_s] or keys[pygame.K_DOWN])
        if dx or dy:
            self.camera.pan(dx * PAN_SPEED * dt, dy * PAN_SPEED * dt)

    # -- drawing ----------------------------------------------------------

    def draw(self, surf) -> None:
        cam, w = self.camera, self.world
        view = self.map_rect()
        surf.set_clip(view)
        layer, wx0, wy0 = self.renderer.layer(cam.zoom, self.tint, self.grid)
        surf.blit(layer, (wx0 * cam.zoom + cam.ox, wy0 * cam.zoom + cam.oy))
        sz = UI.hex_size * cam.zoom
        for st in w.structures:
            x, y = cam.hex_to_screen(st.coord)
            if view.inflate(60, 60).collidepoint(x, y):
                draw_structure(surf, (x, y), sz, st.side, st.kind)
        if self.hover is not None:
            x, y = cam.hex_to_screen(self.hover)
            pygame.draw.polygon(surf, theme.ACCENT, hex_corners(x, y, sz), 2)
        surf.set_clip(None)
        self._draw_panel(surf)

    def _draw_panel(self, surf) -> None:
        wsz = surf.get_size()
        rect = pygame.Rect(wsz[0] - UI.panel_width, 0, UI.panel_width, wsz[1])
        Panel(rect).draw(surf)
        x, y = rect.x + 16, 14
        s = self.world.summary()

        def line(text, size=15, color=theme.TEXT, bold=False, gap=4):
            nonlocal y
            y += Label(text, size, color, bold).draw(surf, (x, y)) + gap

        line("WORLD GENERATOR", 19, theme.ACCENT, True, 8)
        line(f"seed {s['seed']}    preset {s['preset']} ({s['size']})", 16)
        line(f"land {s['land_pct']:.1f}%    retries {s['retries']}", gap=8)
        t = s["terrain"]
        for k in ("PLAIN", "FOREST", "HILL", "MOUNTAIN", "URBAN", "WATER"):
            line(f"  {k.lower():9s}{t[k]:5d}", 14, theme.DIM, gap=1)
        y += 6
        line(f"provinces {s['provinces']}   regions {s['regions']}   cities {s['cities']}")
        line(f"river hexes {s['river_hexes']}   road hexes {s['road_hexes']}", gap=10)

        line("HOVER", 14, theme.ACCENT, True)
        for text in self._hover_lines():
            line(text, 14, gap=2)
        y += 8
        line("STARTING STRUCTURES", 14, theme.ACCENT, True)
        for i, kind in enumerate(STRUCTURE_KINDS):
            cx, cy = x + 12 + (i % 4) * 82, y + 16 + (i // 4) * 40
            draw_structure(surf, (cx, cy), 14, Side.BLUE, kind)
            Label(LEGEND[kind], 12, theme.DIM).draw(surf, (cx + 14, cy - 7))
        y += 90
        for text in HELP:
            line(text, 13, theme.DIM, gap=1)

    def _hover_lines(self) -> List[str]:
        c, w = self.hover, self.world
        if c is None:
            return ["(move the mouse over the map)"]
        k = w.hexmap.terrain[c]
        flags = [f for f, on in (("river", c in w.hexmap.rivers), ("road", c in w.hexmap.roads)) if on]
        out = [f"{c}  {k.name.lower()}" + (f" ({', '.join(flags)})" if flags else "")]
        pid = w.province_of.get(c)
        if pid is not None:
            p = w.provinces[pid]
            own = w.owner.get(pid)
            out.append(f"province {pid}  value {p.value}  capital {p.capital}")
            out.append(f"owner {own.name if own is not None else 'NEUTRAL'}"
                       f"   region {w.region_of[pid]} (bonus {w.regions[w.region_of[pid]].bonus})")
        st = w.structure_at(c)
        if st:
            out.append(f"structure: {st.kind.title()} ({st.side.name})")
        return out
