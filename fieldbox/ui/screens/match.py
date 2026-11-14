"""Match viewer (PLAN.md §5.6): watch a game live, or step through a replay.

The screen only ever reads a `Replay` (plain-data snapshots plus events). In
live mode a `LiveMatch` appends a snapshot whenever the animation finishes, so
scrubbing over turns already played works exactly like replay mode.
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

import pygame

from ...core.hexgrid import distance
from ...params import UIParams
from ...record.replay import LiveMatch, Replay
from ...sim.fog import FOG, radar_range
from ...sim.units import STATS, Side, UnitType
from ...world import generate_world
from .. import theme
from ..app import Screen
from ..camera import SQ3, Camera
from ..hexdraw import MapRenderer, hex_corners
from ..symbols import draw_counter, draw_structure
from ..widgets import Button, Label, Panel

UI = UIParams()
TOP_H, BOTTOM_H = 36, 90
SPEEDS: List[Tuple[str, Optional[float]]] = [("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0),
                                              ("4x", 4.0), ("max", None)]
PAN_SPEED = 700.0
FILTERS = ("all", "combat", "econ", "map", "orders")
KIND_GROUP = {
    "engaged": "combat", "structure_damaged": "combat", "unit_destroyed": "combat",
    "structure_destroyed": "combat",
    "build_queued": "econ", "unit_built": "econ", "fob_started": "econ", "fob_built": "econ",
    "power_low": "econ",
    "province_flipped": "map", "structure_captured": "map", "supply_cut": "map",
    "game_over": "map",
    "order_rejected": "orders", "orders_capped": "orders", "adapted": "orders",
}
LOG_LINE_H = 16
SIDES = (Side.BLUE, Side.RED)


def _default_speed() -> int:
    from ... import settings
    v = settings.load()["anim_speed"]
    return next((i for i, (_, x) in enumerate(SPEEDS) if x == v), 1)


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


class MatchScreen(Screen):
    # -- construction -------------------------------------------------------

    @classmethod
    def create(cls, app, seed: int = 1, preset: str = "tiny", blue: str = "doctrine",
               red: str = "rush", game_seed: int = 0, turns: Optional[int] = None,
               replay: Optional[str] = None, **opts) -> "MatchScreen":
        if replay:
            rep = Replay.load(replay)
            return cls(app, rep.world(), rep, None)
        from ...league.roster import make
        root = opts.get("root") or "runs/evo"
        world = generate_world(seed, preset)
        live = LiveMatch(make(blue, root), make(red, root), world, seed=game_seed,
                         max_turns=turns, blue_name=blue, red_name=red)
        return cls(app, world, live.replay, live)

    def __init__(self, app, world, replay: Replay, live: Optional[LiveMatch]) -> None:
        super().__init__(app)
        self.world, self.rep, self.live = world, replay, live
        self.renderer = MapRenderer(world, UI.hex_size)
        self.camera = Camera(UI.hex_size, UI.zoom_min, UI.zoom_max, UI.zoom_step)
        self.idx = 0                         # displayed snapshot
        self.anim: Optional[float] = None    # None, or 0..1 progress of the move into `idx`
        self.playing = live is not None
        self.speed = _default_speed()
        self.tint, self.grid = True, False
        self.view: Optional[int] = None      # None = God view, 0/1 = what BLUE/RED sees (fog)
        self.rings = False                   # radar range rings
        self._vis_cache: Dict[tuple, tuple] = {}
        self.select: Optional[Tuple[str, int]] = None   # ("unit"|"structure", id)
        self.hover: Optional[Tuple[int, int]] = None
        self.filter = "all"
        self.log_scroll = 0                  # lines scrolled up from the bottom
        self.overlay_open = True
        self._drag = False
        self._scrub = False
        self._names: Dict[int, Tuple[str, int]] = {}       # uid -> (type, side)
        self._snames: Dict[int, str] = {}                   # sid -> structure kind
        self._sside: Dict[int, int] = {}                    # sid -> original owner
        self._indexed = 0
        self._unit_cache: Dict[int, Dict[int, list]] = {}
        self._move_cache: Dict[int, Dict[int, List[Tuple[int, int]]]] = {}
        self._index_new()
        self.buttons: List[Button] = []
        self.fit()

    # -- data access --------------------------------------------------------

    def _index_new(self) -> None:
        for k in range(self._indexed, len(self.rep.snapshots)):
            sn = self.rep.snapshots[k]
            for row in sn["units"]:
                self._names.setdefault(row[0], (row[2], row[1]))
            for row in sn["structures"]:
                self._snames.setdefault(row[0], row[1])
                self._sside.setdefault(row[0], row[2])
        self._indexed = len(self.rep.snapshots)

    def units(self, k: int) -> Dict[int, list]:
        d = self._unit_cache.get(k)
        if d is None:
            d = {row[0]: row for row in self.rep.snapshots[k]["units"]}
            self._unit_cache[k] = d
        return d

    def moves(self, k: int) -> Dict[int, List[Tuple[int, int]]]:
        """uid -> the full path walked during turn k (movement, then any advance)."""
        d = self._move_cache.get(k)
        if d is None:
            d = {}
            for ev in self.rep.events[k]:
                if ev["k"] == "moved":
                    path = [tuple(c) for c in ev["d"]["path"]]
                    uid = ev["d"]["uid"]
                    d[uid] = d[uid] + path[1:] if uid in d else path
            self._move_cache[k] = d
        return d

    @property
    def last(self) -> int:
        return len(self.rep.snapshots) - 1

    @property
    def snap(self) -> dict:
        return self.rep.snapshots[self.idx]

    @property
    def at_end(self) -> bool:
        return self.rep.finished and self.idx == self.last and self.anim is None

    # -- layout -------------------------------------------------------------

    def window_size(self) -> Tuple[int, int]:
        return self.app.window.get_size()

    def map_rect(self) -> pygame.Rect:
        w, h = self.window_size()
        return pygame.Rect(0, TOP_H, w - UI.panel_width, h - TOP_H - BOTTOM_H)

    def fit(self) -> None:
        r = self.map_rect()
        self.camera.fit(self.renderer.bbox, (r.x, r.y, r.w, r.h))

    def _bottom_rect(self) -> pygame.Rect:
        w, h = self.window_size()
        return pygame.Rect(0, h - BOTTOM_H, w - UI.panel_width, BOTTOM_H)

    def _timeline_rect(self) -> pygame.Rect:
        b = self._bottom_rect()
        return pygame.Rect(b.x + 16, b.y + 46, b.w - 32, 34)

    def _layout_buttons(self) -> None:
        b = self._bottom_rect()
        y = b.y + 9
        self.buttons = []
        x = b.x + 16

        def add(label, fn, w=64):
            nonlocal x
            self.buttons.append(Button(pygame.Rect(x, y, w, 28), label, fn))
            x += w + 6

        add("Start", lambda: self.goto(0))
        add("Back", lambda: self.step(-1))
        add("Pause" if self.playing else "Play", self.toggle_play, 70)
        add("Step", lambda: self.step(1))
        add("End", lambda: self.goto(self.last))
        x += 18
        for i, (label, _) in enumerate(SPEEDS):
            add(label, lambda i=i: self.set_speed(i), 52)

    # -- control ------------------------------------------------------------

    @property
    def has_fog(self) -> bool:
        return "vis" in self.rep.snapshots[0]       # version 1 replays have none

    def cycle_view(self) -> None:
        """God view -> BLUE's view -> RED's view -> God view."""
        if not self.has_fog:
            return
        self.view = {None: 0, 0: 1, 1: None}[self.view]
        self.select = None

    def visible_sets(self):
        """(land, air/sea) visible hex sets for the current view and turn."""
        key = (self.idx, self.view)
        got = self._vis_cache.get(key)
        if got is None:
            land, air = self.snap["vis"][self.view]
            got = (set(map(tuple, land)), set(map(tuple, air)))
            if len(self._vis_cache) > 64:
                self._vis_cache.clear()
            self._vis_cache[key] = got
        return got

    def unit_visible(self, row) -> bool:
        """Would the current view see this unit row? (always, in God view)"""
        if self.view is None or row[1] == self.view:
            return True
        land, air = self.visible_sets()
        dom = STATS[UnitType[row[2]]].domain.name
        return (row[3], row[4]) in (land if dom == "LAND" else air)

    def toggle_play(self) -> None:
        self.playing = not self.playing

    def set_speed(self, i: int) -> None:
        self.speed = max(0, min(len(SPEEDS) - 1, i))

    def goto(self, k: int, anim: Optional[float] = None) -> None:
        """Jump to snapshot k (clamped to what exists); optionally freeze
        mid-animation at `anim` (used by screenshots)."""
        self.idx = max(0, min(self.last, k))
        self.anim = anim if self.idx > 0 else None
        self.log_scroll = 0

    def step(self, d: int) -> None:
        self.playing = False
        if d > 0:
            self._forward()
        else:
            self.goto(self.idx - 1)

    def advance_to(self, k: int, anim: Optional[float] = None) -> None:
        """Play (live) or jump (replay) to turn k without animating."""
        while self.live is not None and not self.live.done and self.last < k:
            self.live.step()
        self._index_new()
        self.goto(k, anim)

    def _forward(self) -> bool:
        """Show the next turn, stepping the live game if needed."""
        if self.idx >= self.last and self.live is not None and not self.live.done:
            self.live.step()
            self._index_new()
        if self.idx < self.last:
            self.idx += 1
            self.anim = 0.0 if self.speed_value is not None else None
            self.log_scroll = 0
            return True
        return False

    @property
    def speed_value(self) -> Optional[float]:
        return SPEEDS[self.speed][1]

    # -- events -------------------------------------------------------------

    def handle_event(self, e) -> None:
        for b in self.buttons:
            if b.handle_event(e):
                return
        if e.type == pygame.KEYDOWN:
            k = e.key
            if k == pygame.K_ESCAPE:
                self.app.pop()
            elif k == pygame.K_SPACE:
                self.toggle_play()
            elif k == pygame.K_RIGHT:
                self.step(1)
            elif k == pygame.K_LEFT:
                self.step(-1)
            elif k == pygame.K_HOME:
                self.goto(0)
            elif k == pygame.K_END:
                self.goto(self.last)
            elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self.set_speed(self.speed + 1)
            elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.set_speed(self.speed - 1)
            elif k == pygame.K_z:
                self.fit()
            elif k == pygame.K_f:
                self.cycle_view()
            elif k == pygame.K_r:
                self.rings = not self.rings
            elif k == pygame.K_g:
                self.grid = not self.grid
            elif k == pygame.K_p:
                self.tint = not self.tint
            elif k in (pygame.K_o, pygame.K_RETURN):
                self.overlay_open = not self.overlay_open
        elif e.type == pygame.MOUSEWHEEL:
            pos = pygame.mouse.get_pos()
            if self.map_rect().collidepoint(pos):
                self.camera.zoom_by(e.y, pos)
            elif pos[0] >= self.window_size()[0] - UI.panel_width:
                self.log_scroll = max(0, self.log_scroll + 3 * e.y)
        elif e.type == pygame.MOUSEBUTTONDOWN:
            if e.button in (2, 3):
                self._drag = True
            elif e.button == 1:
                self._click(e.pos)
        elif e.type == pygame.MOUSEBUTTONUP:
            if e.button in (2, 3):
                self._drag = False
            elif e.button == 1:
                self._scrub = False
        elif e.type == pygame.MOUSEMOTION:
            if self._drag:
                self.camera.pan(*e.rel)
            if self._scrub:
                self._scrub_to(e.pos[0])
            self.hover = None
            if self.map_rect().collidepoint(e.pos):
                c = self.camera.screen_to_hex(*e.pos)
                if self.world.hexmap.in_bounds(c):
                    self.hover = c

    def _click(self, pos) -> None:
        if self._timeline_rect().inflate(0, 10).collidepoint(pos):
            self._scrub = True
            self._scrub_to(pos[0])
            return
        panel_x = self.window_size()[0] - UI.panel_width
        if pos[0] >= panel_x:
            for name, rect in self._chip_rects():
                if rect.collidepoint(pos):
                    self.filter = name
                    self.log_scroll = 0
            return
        if self.map_rect().collidepoint(pos):
            self.select = self._pick(pos)

    def _scrub_to(self, x: int) -> None:
        r = self._timeline_rect()
        total = max(self.rep.meta.get("max_turns", self.last), self.last, 1)
        k = round((x - r.x) / max(r.w, 1) * total)
        self.playing = False
        self.goto(k)

    def _pick(self, pos) -> Optional[Tuple[str, int]]:
        sz = UI.hex_size * self.camera.zoom
        best, best_d = None, (0.7 * sz) ** 2
        for row in self.snap["units"]:
            if not self.unit_visible(row):
                continue
            x, y = self._unit_screen(row)
            d = (x - pos[0]) ** 2 + (y - pos[1]) ** 2
            if d < best_d:
                best, best_d = ("unit", row[0]), d
        if best:
            return best
        for row in self._structure_rows():
            x, y = self.camera.hex_to_screen((row[3], row[4]))
            if (x - pos[0]) ** 2 + (y - pos[1]) ** 2 < (0.7 * sz) ** 2:
                return ("structure", row[0])
        return None

    # -- update -------------------------------------------------------------

    def update(self, dt: float) -> None:
        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_a]) - (keys[pygame.K_d])
        dy = (keys[pygame.K_w]) - (keys[pygame.K_s])
        if dx or dy:
            self.camera.pan(dx * PAN_SPEED * dt, dy * PAN_SPEED * dt)
        sp = self.speed_value
        if self.anim is not None:
            self.anim = 1.0 if sp is None else self.anim + dt * sp / (UI.turn_anim_ms / 1000.0)
            if self.anim >= 1.0:
                self.anim = None
        if self.playing and self.anim is None:
            if sp is None:                     # max: as many turns as fit in a frame
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < 0.012 and self._forward():
                    pass
                self.anim = None
                if self.idx == self.last and self.rep.finished:
                    self.playing = False
            elif not self._forward() and self.rep.finished:
                self.playing = False            # the game is over: leave the button on Play
        self._layout_buttons()

    # -- drawing: world -----------------------------------------------------

    def _unit_screen(self, row) -> Tuple[float, float]:
        x, y = self.camera.hex_to_screen((row[3], row[4]))
        ox, oy = self._domain_offset(row[2], UI.hex_size * self.camera.zoom)
        return x + ox, y + oy

    def _animated(self, uid: int, row_now, row_prev, t: Optional[float]) -> Tuple[float, float]:
        """Screen position of a unit at animation progress t."""
        cam = self.camera
        row = row_now or row_prev
        end = cam.hex_to_screen((row[3], row[4]))
        if t is None or row_now is None:
            return end
        path = self.moves(self.idx).get(uid)
        if not path or len(path) < 2:
            return end
        pts = [cam.hex_to_screen(c) for c in path]
        f = _ease(min(1.0, t / 0.75)) * (len(pts) - 1)
        i = min(int(f), len(pts) - 2)
        u = f - i
        return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * u,
                pts[i][1] + (pts[i + 1][1] - pts[i][1]) * u)

    @staticmethod
    def _domain_offset(utype: str, sz: float) -> Tuple[float, float]:
        d = STATS[UnitType[utype]].domain.name
        return {"AIR": (0.28 * sz, -0.28 * sz), "SEA": (-0.28 * sz, 0.28 * sz)}.get(d, (0.0, 0.0))

    def _structure_rows(self) -> List[list]:
        """Structure rows [sid, kind, side, q, r, strength, age] the current
        view knows about: all of them in God view; else own structures plus the
        enemy structures seen so far (age > 0 = last seen that long ago)."""
        rows = [list(r) + [0] for r in self.snap["structures"]]
        if self.view is None:
            return rows
        known = {r[0]: r for r in self.snap["known"][self.view]}
        out = [r for r in rows if r[2] == self.view]
        out += [list(r[:6]) + [r[6]] for r in known.values()]
        return out

    def _draw_fog(self, surf: pygame.Surface, view_rect: pygame.Rect) -> None:
        """Darken what the current view cannot see; radar-only hexes less."""
        land, air = self.visible_sets()
        cam = self.camera
        sz = UI.hex_size * cam.zoom
        shade = pygame.Surface(view_rect.size, pygame.SRCALPHA)
        ox, oy = view_rect.topleft
        for c in self.renderer.coords:
            if c in land:
                continue
            x, y = cam.hex_to_screen(c)
            if not view_rect.inflate(80, 80).collidepoint(x, y):
                continue
            alpha = 70 if c in air else 150
            pygame.draw.polygon(shade, (8, 10, 14, alpha),
                                [(px - ox, py - oy) for px, py in hex_corners(x, y, sz + 0.5)])
        surf.blit(shade, view_rect.topleft)

    def _draw_rings(self, surf: pygame.Surface) -> None:
        cam, sn = self.camera, self.snap
        sz = UI.hex_size * cam.zoom
        dc = {s: any(r[1] == "DATACENTER" and r[2] == s for r in sn["structures"]) for s in (0, 1)}
        for r in sn["structures"]:
            if r[1] != "RADAR" or (self.view is not None and r[2] != self.view):
                continue
            x, y = cam.hex_to_screen((r[3], r[4]))
            for base, col in ((FOG.radar_range_ground, theme.SIDE_FILL[Side(r[2])]),
                              (FOG.radar_range_air_sea, theme.SIDE_LIGHT[Side(r[2])])):
                rng = radar_range(base, r[5], sn["power"][r[2]], dc[r[2]])
                if rng > 0:
                    pygame.draw.circle(surf, col, (round(x), round(y)),
                                       round((rng + 0.5) * SQ3 * sz), 2)

    def _draw_world(self, surf: pygame.Surface) -> None:
        cam, view = self.camera, self.map_rect()
        owner = {i: (None if v < 0 else Side(v)) for i, v in enumerate(self.snap["owner"])}
        surf.set_clip(view)
        layer, wx0, wy0 = self.renderer.layer(cam.zoom, self.tint, self.grid, owner)
        surf.blit(layer, (wx0 * cam.zoom + cam.ox, wy0 * cam.zoom + cam.oy))
        sz = UI.hex_size * cam.zoom
        t = self.anim
        cur = self.units(self.idx)
        prev = self.units(self.idx - 1) if self.idx > 0 else {}
        show_prev = t is not None
        margin = view.inflate(80, 80)

        # structures (those destroyed this turn linger until the burst)
        rows = self._structure_rows()
        if show_prev and t < 0.8 and self.view is None:
            alive = {r[0] for r in rows}
            rows += [list(r) + [0] for r in self.rep.snapshots[self.idx - 1]["structures"]
                     if r[0] not in alive]
        sel = self.select
        for r in rows:
            x, y = cam.hex_to_screen((r[3], r[4]))
            if margin.collidepoint(x, y):
                draw_structure(surf, (x, y), sz, Side(r[2]), r[1], r[5], ring=r[5] < 99.5)
                if r[6] > 0:                       # remembered, not currently seen
                    surf.blit(theme.font(max(10, round(sz * 0.6))).render(f"?{r[6]}", True, theme.WARN),
                              (x + sz * 0.5, y - sz * 0.9))
                if sel == ("structure", r[0]):
                    pygame.draw.polygon(surf, theme.ACCENT, hex_corners(x, y, sz), 3)

        # units: sea, land, air so that aircraft draw on top
        order = {"SEA": 0, "LAND": 1, "AIR": 2}
        draw_rows = [(r, r) for r in cur.values() if self.unit_visible(r)]
        if show_prev and t < 0.8:
            draw_rows += [(None, r) for uid, r in prev.items()
                          if uid not in cur and self.unit_visible(r)]
        draw_rows.sort(key=lambda p: (order[STATS[UnitType[(p[0] or p[1])[2]]].domain.name],
                                      (p[0] or p[1])[0]))
        pos: Dict[int, Tuple[float, float]] = {}
        for now, row in draw_rows:
            uid = row[0]
            x, y = self._animated(uid, now, prev.get(uid), t)
            ox, oy = self._domain_offset(row[2], sz)
            x, y = x + ox, y + oy
            pos[uid] = (x, y)
            if not margin.collidepoint(x, y):
                continue
            draw_counter(surf, (x, y), sz, Side(row[1]), row[2], row[5], not row[7])
            if sel == ("unit", uid):
                pygame.draw.circle(surf, theme.ACCENT, (round(x), round(y)), round(0.75 * sz), 2)

        if self.view is not None:
            self._draw_fog(surf, view)
            for g in self.snap["ghosts"][self.view]:        # lost contacts, translucent with their age
                x, y = cam.hex_to_screen((g[2], g[3]))
                ox, oy = self._domain_offset(g[1], sz)
                if margin.collidepoint(x + ox, y + oy):
                    draw_counter(surf, (x + ox, y + oy), sz, Side(1 - self.view), g[1], g[4],
                                 ghost_age=g[5])
        if self.rings:
            self._draw_rings(surf)
        if self.hover is not None:
            hx, hy = cam.hex_to_screen(self.hover)
            pygame.draw.polygon(surf, theme.TEXT, hex_corners(hx, hy, sz), 1)
        if t is not None:
            self._draw_fire(surf, pos, t, sz)
        surf.set_clip(None)

    def _draw_fire(self, surf, pos, t: float, sz: float) -> None:
        """Engagement lines in the second half of the turn, kill bursts at the end."""
        cam = self.camera
        sdict = {r[0]: r for r in self.snap["structures"]}
        if self.idx > 0:
            for r in self.rep.snapshots[self.idx - 1]["structures"]:
                sdict.setdefault(r[0], r)
        for ev in self.rep.events[self.idx]:
            k, d = ev["k"], ev["d"]
            if k in ("engaged", "structure_damaged") and t >= 0.4:
                a = pos.get(d["attacker"])
                if k == "engaged":
                    b = pos.get(d["defender"])
                else:
                    s = sdict.get(d["sid"])
                    b = cam.hex_to_screen((s[3], s[4])) if s else None
                if a and b:
                    col = theme.SIDE_LIGHT[Side(ev["s"])] if ev["s"] in (0, 1) else theme.TEXT
                    pygame.draw.line(surf, col, a, b, max(2, round(sz / 8)))
                    pygame.draw.circle(surf, theme.ACCENT, (round(b[0]), round(b[1])), max(3, round(sz / 5)))
            elif k in ("unit_destroyed", "structure_destroyed") and t >= 0.55:
                if k == "unit_destroyed":
                    c = pos.get(d["uid"])
                else:
                    s = sdict.get(d["sid"])
                    c = cam.hex_to_screen((s[3], s[4])) if s else None
                if c:
                    r = sz * (0.3 + 1.1 * (t - 0.55) / 0.45)
                    pygame.draw.circle(surf, theme.BAD, (round(c[0]), round(c[1])), round(r), 3)

    # -- drawing: chrome ----------------------------------------------------

    def draw(self, surf: pygame.Surface) -> None:
        self._draw_world(surf)
        self._draw_topbar(surf)
        self._draw_bottom(surf)
        self._draw_panel(surf)
        if self.at_end and self.overlay_open:
            self._draw_overlay(surf)

    def _draw_topbar(self, surf) -> None:
        w = self.window_size()[0] - UI.panel_width
        pygame.draw.rect(surf, theme.PANEL, pygame.Rect(0, 0, w, TOP_H))
        pygame.draw.line(surf, theme.PANEL_BORDER, (0, TOP_H - 1), (w, TOP_H - 1))
        sn = self.snap
        total = self.rep.meta.get("max_turns", "?")
        Label(f"T {sn['turn']}/{total}", 17, theme.ACCENT, True).draw(surf, (12, 8))
        if self.has_fog:
            vtxt = "GOD VIEW" if self.view is None else f"{Side(self.view).name} VIEW"
            vimg = theme.font(13, True).render(vtxt, True, theme.ACCENT)
            surf.blit(vimg, ((w - vimg.get_width()) // 2, 11))
        for i, side in enumerate(SIDES):
            name = str(self.rep.meta.get("blue" if i == 0 else "red", side.name)).upper()
            if self.view is not None and self.view != i:     # the enemy's economy is hidden
                text = f"{name}  PP ?  CP ?  PWR ?  VP {sn['vp'][i]:.0f}"
            else:
                text = (f"{name}  PP {sn['pp'][i]:.0f} (+{sn['income'][i]:.0f})  CP {sn['cp'][i]}  "
                        f"PWR {sn['power'][i] * 100:.0f}%  VP {sn['vp'][i]:.0f}")
            img = theme.font(14, True).render(text, True, theme.SIDE_LIGHT[side])
            x = 110 if i == 0 else w - img.get_width() - 12
            surf.blit(img, (x, 10))

    def _draw_bottom(self, surf) -> None:
        b = self._bottom_rect()
        pygame.draw.rect(surf, theme.PANEL, b)
        pygame.draw.line(surf, theme.PANEL_BORDER, (b.x, b.y), (b.right, b.y))
        for i, btn in enumerate(self.buttons):
            btn.draw(surf)
            if i >= 5 and i - 5 == self.speed:
                pygame.draw.rect(surf, theme.ACCENT, btn.rect, 3)
        # timeline with a VP-difference sparkline
        r = self._timeline_rect()
        pygame.draw.rect(surf, theme.BG, r)
        snaps = self.rep.snapshots
        total = max(self.rep.meta.get("max_turns", len(snaps) - 1), len(snaps) - 1, 1)
        diffs = [s["vp"][0] - s["vp"][1] for s in snaps]
        peak = max([abs(d) for d in diffs] + [1.0])
        mid = r.centery
        pygame.draw.line(surf, theme.PANEL_BORDER, (r.x, mid), (r.right, mid))
        pts = [(r.x + r.w * k / total, mid - diffs[k] / peak * (r.h / 2 - 2)) for k in range(len(snaps))]
        if len(pts) > 1:
            pygame.draw.lines(surf, theme.DIM, False, pts, 2)
        px = r.x + r.w * self.idx / total
        pygame.draw.line(surf, theme.ACCENT, (px, r.y), (px, r.bottom), 2)
        pygame.draw.rect(surf, theme.PANEL_BORDER, r, 1)
        Label("BLUE ahead", 11, theme.SIDE_LIGHT[Side.BLUE]).draw(surf, (r.x + 4, r.y + 1))
        Label("RED ahead", 11, theme.SIDE_LIGHT[Side.RED]).draw(surf, (r.x + 4, r.bottom - 14))

    # -- drawing: panel -----------------------------------------------------

    def _chip_rects(self) -> List[Tuple[str, pygame.Rect]]:
        x0 = self.window_size()[0] - UI.panel_width + 16
        y = 232
        out, x = [], x0
        for name in FILTERS:
            wdt = 14 + 8 * len(name)
            out.append((name, pygame.Rect(x, y, wdt, 20)))
            x += wdt + 6
        return out

    def _draw_panel(self, surf) -> None:
        ww, wh = self.window_size()
        rect = pygame.Rect(ww - UI.panel_width, 0, UI.panel_width, wh)
        Panel(rect).draw(surf)
        x, y = rect.x + 16, 12

        def line(text, size=14, color=theme.TEXT, bold=False, gap=3):
            nonlocal y
            y += Label(text, size, color, bold).draw(surf, (x, y)) + gap

        line("SELECTION", 14, theme.ACCENT, True, 6)
        for text, col in self._card_lines():
            line(text, 14, col, gap=2)
        # event log
        y = 210
        line("EVENT LOG", 14, theme.ACCENT, True, 4)
        for name, cr in self._chip_rects():
            on = name == self.filter
            pygame.draw.rect(surf, theme.PANEL_BORDER if on else theme.BG, cr)
            pygame.draw.rect(surf, theme.ACCENT if on else theme.PANEL_BORDER, cr, 1)
            img = theme.font(12).render(name, True, theme.TEXT if on else theme.DIM)
            surf.blit(img, img.get_rect(center=cr.center))
        log_top = 262
        lines = self._log_lines()
        room = max(1, (wh - log_top - 8) // LOG_LINE_H)
        self.log_scroll = max(0, min(self.log_scroll, max(0, len(lines) - room)))
        end = len(lines) - self.log_scroll
        shown = lines[max(0, end - room):end]
        surf.set_clip(pygame.Rect(rect.x + 1, log_top, rect.w - 2, wh - log_top))
        yy = log_top
        for text, col in shown:
            img = theme.font(12).render(text, True, col)
            surf.blit(img, (x, yy))
            yy += LOG_LINE_H
        surf.set_clip(None)
        if self.log_scroll:
            Label(f"{self.log_scroll} newer lines below", 11, theme.DIM).draw(surf, (x, wh - 18))

    def _card_lines(self) -> List[Tuple[str, tuple]]:
        sn, sel = self.snap, self.select
        if sel and sel[0] == "unit":
            row = self.units(self.idx).get(sel[1])
            if row is None:
                return [(f"unit #{sel[1]}: destroyed", theme.BAD)]
            st = STATS[UnitType[row[2]]]
            side = Side(row[1])
            terr = self.world.hexmap.terrain.get((row[3], row[4]))
            return [
                (f"{row[2].title()} #{row[0]}  ({side.name})", theme.SIDE_LIGHT[side]),
                (f"hex ({row[3]},{row[4]})  {terr.name.lower() if terr else ''}", theme.TEXT),
                (f"strength {row[5]:.0f}   supply {row[6]:.0f}" + ("" if row[7] else "  (CUT OFF)"),
                 theme.TEXT if row[7] else theme.BAD),
                (f"entrench {row[8]}   move {st.move:g}  reach {st.reach}  spot {st.spot}", theme.DIM),
                (f"order: {row[9] or '-'}", theme.TEXT),
                (f"task: {(row[10] if len(row) > 10 else '') or '-'}", theme.TEXT),
            ]
        if sel and sel[0] == "structure":
            row = next((r for r in self._structure_rows() if r[0] == sel[1]), None)
            if row is None:
                return [(f"structure S{sel[1]}: destroyed", theme.BAD)]
            side = Side(row[2])
            out = [(f"{row[1].title()} S{row[0]}  ({side.name})", theme.SIDE_LIGHT[side]),
                   (f"hex ({row[3]},{row[4]})", theme.TEXT),
                   (f"strength {row[5]:.0f}" + (f"  (last seen {row[6]} turns ago)" if row[6] else ""),
                    theme.TEXT)]
            q = sn.get("queues", {}).get(str(row[0]))
            if q and (self.view is None or row[2] == self.view):
                out.append(("queue: " + ", ".join(f"{n[:3]} {left}" for n, left in q), theme.DIM))
            return out
        if self.hover is not None:
            h = self.hover
            terr = self.world.hexmap.terrain[h]
            out = [(f"hex {h}  {terr.name.lower()}", theme.DIM)]
            pid = self.world.province_of.get(h)
            if pid is not None:
                o = sn["owner"][pid]
                out.append((f"province {pid}  owner {'NEUTRAL' if o < 0 else Side(o).name}", theme.DIM))
            return out
        return [("click a unit or structure", theme.DIM)]

    # -- event log ----------------------------------------------------------

    def _uname(self, uid) -> str:
        t = self._names.get(uid)
        return f"{STATS[UnitType[t[0]]].label}#{uid}" if t else f"#{uid}"

    def describe(self, ev: dict) -> Optional[str]:
        k, d = ev["k"], ev["d"]
        if k == "engaged":
            return (f"{self._uname(d['attacker'])} {'fires on' if d['mode'] == 'fire' else 'hits'} "
                    f"{self._uname(d['defender'])}  -{d['defender_loss']:.0f}/-{d['attacker_loss']:.0f}")
        if k == "structure_damaged":
            return f"{self._uname(d['attacker'])} hits {self._snames.get(d['sid'], '?')} S{d['sid']} -{d['loss']:.0f}"
        if k == "unit_destroyed":
            return f"{STATS[UnitType[d['utype']]].label}#{d['uid']} destroyed ({d['cause']})"
        if k == "structure_destroyed":
            return f"{d['structure']} S{d['sid']} destroyed (+{d['vp']} VP)"
        if k == "structure_captured":
            return f"{d['structure']} S{d['sid']} captured"
        if k == "province_flipped":
            return f"province {d['province']} taken"
        if k == "build_queued":
            return f"queued {d['utype'].title()}"
        if k == "unit_built":
            return f"{d['utype'].title()} built"
        if k == "fob_started":
            return "FOB construction started"
        if k == "fob_built":
            return "FOB built"
        if k == "supply_cut":
            return f"{self._uname(d['uid'])} cut off"
        if k == "order_rejected":
            return f"order rejected: {d['reason']}"
        if k == "adapted":
            if d.get("mode") == "defend":
                return "adapted: defend " + ", ".join(d.get("kinds", [])) if d.get("kinds") else "adapted: defence reset"
            sh = ", ".join(f"{n.lower()} +{v:.2f}" for n, v in d.get("shifts", {}).items())
            return f"adapted: build {sh or 'reset'}"
        if k == "orders_capped":
            return f"CP cap: {d['dropped']} orders dropped"
        if k == "power_low":
            return f"power {d['factor'] * 100:.0f}%"
        if k == "game_over":
            return f"GAME OVER: {d.get('winner') or 'draw'} ({d['cause']})"
        return None

    def _event_known(self, ev: dict) -> bool:
        """Would the viewed side know about this event? Its own, anything done
        to it, and public ones (provinces, game over)."""
        v, k, d = self.view, ev["k"], ev["d"]
        if ev["s"] == v or k in ("province_flipped", "game_over"):
            return True
        if k == "engaged":
            return self._names.get(d["defender"], ("", -1))[1] == v
        if k == "structure_damaged":
            return self._snames.get(d["sid"]) is not None and self._sside.get(d["sid"]) == v
        return False

    def _log_lines(self) -> List[Tuple[str, tuple]]:
        out = []
        for k in range(1, self.idx + 1):
            for ev in self.rep.events[k]:
                group = KIND_GROUP.get(ev["k"])
                if group is None or (self.filter != "all" and group != self.filter):
                    continue
                if self.view is not None and not self._event_known(ev):
                    continue
                text = self.describe(ev)
                if text is None:
                    continue
                s = ev["s"]
                col = theme.SIDE_LIGHT[Side(s)] if s in (0, 1) else theme.TEXT
                out.append((f"T{k:<3}{text}", col))
        return out

    # -- end overlay --------------------------------------------------------

    def summary(self) -> dict:
        """Key stats for the end overlay, from the recorded events."""
        lost = {s: {} for s in (0, 1)}
        struct_lost = {0: 0, 1: 0}
        for evs in self.rep.events:
            for ev in evs:
                if ev["k"] == "unit_destroyed":
                    lab = STATS[UnitType[ev["d"]["utype"]]].label
                    lost[ev["s"]][lab] = lost[ev["s"]].get(lab, 0) + 1
                elif ev["k"] == "structure_destroyed":
                    struct_lost[ev["s"]] += 1
        owner = self.rep.snapshots[-1]["owner"]
        return {"lost": lost, "structures_lost": struct_lost,
                "provinces": [owner.count(0), owner.count(1), owner.count(-1)],
                "result": self.rep.meta.get("result", {})}

    def _draw_overlay(self, surf) -> None:
        s = self.summary()
        res = s["result"]
        mw = self.window_size()[0] - UI.panel_width
        box = pygame.Rect(0, 0, 640, 330)
        box.center = (mw // 2, TOP_H + (self.window_size()[1] - TOP_H - BOTTOM_H) // 2)
        shade = pygame.Surface(box.size, pygame.SRCALPHA)
        shade.fill((20, 24, 28, 235))
        surf.blit(shade, box.topleft)
        pygame.draw.rect(surf, theme.ACCENT, box, 2)
        winner = res.get("winner", "DRAW")
        col = theme.SIDE_LIGHT[Side[winner]] if winner in ("BLUE", "RED") else theme.TEXT
        x, y = box.x + 24, box.y + 18
        y += Label("DRAW" if winner == "DRAW" else f"{winner} WINS", 28, col, True).draw(surf, (x, y)) + 4
        cause = str(res.get("cause", "")).replace("_", " ")
        vp = res.get("vp", [0, 0])
        y += Label(f"by {cause} after {res.get('turns', '?')} turns   VP {vp[0]:.0f} - {vp[1]:.0f}",
                   15, theme.TEXT).draw(surf, (x, y)) + 12
        for i, side in enumerate(SIDES):
            name = str(self.rep.meta.get("blue" if i == 0 else "red", side.name))
            lost = ", ".join(f"{k} {v}" for k, v in sorted(s["lost"][i].items())) or "none"
            y += Label(f"{side.name} ({name})", 15, theme.SIDE_LIGHT[side], True).draw(surf, (x, y)) + 2
            y += Label(f"  provinces held {s['provinces'][i]}   structures lost {s['structures_lost'][i]}",
                       14, theme.TEXT).draw(surf, (x, y)) + 2
            y += Label(f"  units lost: {lost}", 13, theme.DIM).draw(surf, (x, y)) + 8
        Label("O / Enter closes this   |   Home restarts the replay", 12, theme.DIM).draw(
            surf, (x, box.bottom - 26))
