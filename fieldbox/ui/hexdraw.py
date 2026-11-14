"""Terrain rendering (PLAN.md §5.4): one cached surface per zoom value.

The whole map (terrain, rivers, roads, province borders, ownership tint) is
drawn once per (zoom, tint, grid) and blitted; only icons and highlights are
drawn per frame.
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from typing import Dict, List, Tuple

import pygame

from ..core.hexgrid import Coord, Terrain, neighbors
from ..world.worldmap import WorldMap
from . import theme
from .camera import SQ3, hex_to_world

CACHE_SIZE = 2          # surfaces kept (each can be tens of MB at high zoom)
DECOR_MIN_PX = 9        # draw terrain glyphs only when a hex is at least this big


def hex_corners(cx: float, cy: float, sz: float) -> List[Tuple[float, float]]:
    return [(cx + sz * math.cos(math.radians(60 * k - 30)),
             cy + sz * math.sin(math.radians(60 * k - 30))) for k in range(6)]


class MapRenderer:
    def __init__(self, world: WorldMap, s: float) -> None:
        self.world, self.s = world, s
        t = world.hexmap.terrain
        self.coords = sorted(t, key=lambda c: (c[1], c[0]))
        # Water depth: hex distance to the nearest land (1 = shore).
        self.depth: Dict[Coord, int] = {}
        queue = deque()
        for c in self.coords:
            if t[c] is not Terrain.WATER:
                self.depth[c] = 0
                queue.append(c)
        while queue:
            cur = queue.popleft()
            for n in neighbors(*cur):
                if n in t and n not in self.depth:
                    self.depth[n] = self.depth[cur] + 1
                    queue.append(n)
        xs = [hex_to_world(c, s)[0] for c in self.coords]
        h = world.height
        self.bbox = (min(xs) - s * SQ3 / 2, -s, max(xs) + s * SQ3 / 2, s * 1.5 * (h - 1) + s)
        self._cache: "OrderedDict[tuple, pygame.Surface]" = OrderedDict()

    def layer(self, zoom: float, tint: bool, grid: bool) -> Tuple[pygame.Surface, float, float]:
        """(surface, world_x0, world_y0): blit at world0 * zoom + camera offset."""
        key = (round(zoom, 4), tint, grid)
        surf = self._cache.get(key)
        if surf is None:
            surf = self._build(zoom, tint, grid)
            self._cache[key] = surf
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return surf, self.bbox[0], self.bbox[1]

    # -- drawing ----------------------------------------------------------

    def _build(self, z: float, tint: bool, grid: bool) -> pygame.Surface:
        w, s = self.world, self.s
        t = w.hexmap.terrain
        minx, miny, maxx, maxy = self.bbox
        surf = pygame.Surface((math.ceil((maxx - minx) * z) + 2, math.ceil((maxy - miny) * z) + 2))
        surf.fill(theme.BG)
        sz = s * z

        def pos(c: Coord) -> Tuple[float, float]:
            x, y = hex_to_world(c, s)
            return (x - minx) * z, (y - miny) * z

        for c in self.coords:
            x, y = pos(c)
            k = t[c]
            if k is Terrain.WATER:
                d = min(self.depth[c], 3)
                col = theme.mix(theme.WATER_SHALLOW, theme.WATER_DEEP, (d - 1) / 2.0)
            else:
                col = theme.TERRAIN[k.name]
                if tint:
                    side = w.owner.get(w.province_of.get(c, -1))
                    if side is not None:
                        col = theme.mix(col, theme.SIDE_FILL[side], theme.TINT)
            pts = hex_corners(x, y, sz + 0.5)
            pygame.draw.polygon(surf, col, pts)
            pygame.draw.polygon(surf, col, pts, 1)          # close hairline gaps
            if grid:
                pygame.draw.polygon(surf, theme.mix(col, (0, 0, 0), 0.35), hex_corners(x, y, sz), 1)
            if sz >= DECOR_MIN_PX:
                self._decorate(surf, k, x, y, sz, col)

        self._borders(surf, pos, sz, tint, z)
        width = max(2, round(3 * z))
        for path in w.river_paths:
            self._polyline(surf, theme.RIVER, [pos(c) for c in path], width)
        for path in w.road_paths:
            self._polyline(surf, theme.ROAD, [pos(c) for c in path], max(1, round(2 * z)))
        return surf

    @staticmethod
    def _polyline(surf, color, pts, width) -> None:
        if len(pts) > 1:
            pygame.draw.lines(surf, color, False, pts, width)
        for p in pts:
            pygame.draw.circle(surf, color, (round(p[0]), round(p[1])), max(1, width // 2))

    @staticmethod
    def _decorate(surf, kind: Terrain, x: float, y: float, sz: float, col) -> None:
        if kind is Terrain.MOUNTAIN:
            pygame.draw.polygon(surf, (0xD0, 0xD0, 0xD0),
                                [(x, y - .5 * sz), (x - .42 * sz, y + .3 * sz), (x + .42 * sz, y + .3 * sz)])
            pygame.draw.polygon(surf, (0x6A, 0x6A, 0x6A),
                                [(x, y - .5 * sz), (x + .42 * sz, y + .3 * sz), (x + .1 * sz, y + .3 * sz)])
        elif kind is Terrain.HILL:
            dark = theme.mix(col, (0, 0, 0), 0.30)
            pygame.draw.arc(surf, dark, pygame.Rect(x - .45 * sz, y - .15 * sz, .9 * sz, .6 * sz),
                            0.2, math.pi - 0.2, max(1, round(sz / 12)))
        elif kind is Terrain.FOREST:
            dark = theme.mix(col, (0, 0, 0), 0.35)
            pygame.draw.polygon(surf, dark, [(x, y - .4 * sz), (x - .3 * sz, y + .25 * sz), (x + .3 * sz, y + .25 * sz)])
        elif kind is Terrain.URBAN:
            dark = theme.mix(col, (0, 0, 0), 0.40)
            b = .26 * sz
            for dx in (-.2, .2):
                for dy in (-.2, .2):
                    pygame.draw.rect(surf, dark, pygame.Rect(x + dx * sz - b / 2, y + dy * sz - b / 2, b, b))

    def _borders(self, surf, pos, sz: float, tint: bool, z: float) -> None:
        w = self.world
        thin = max(1, round(z * 1.2))
        thick = max(2, round(3 * z))
        for c, p in sorted(w.province_of.items()):
            cx, cy = pos(c)
            for n in neighbors(*c):
                q = w.province_of.get(n)
                if q is None or q == p or n < c:
                    continue
                nx, ny = pos(n)
                mx, my = (cx + nx) / 2, (cy + ny) / 2
                vx, vy = nx - cx, ny - cy
                ln = math.hypot(vx, vy) or 1.0
                px, py = -vy / ln * sz / 2, vx / ln * sz / 2     # half an edge, along the border
                a, b = (mx + px, my + py), (mx - px, my - py)
                pygame.draw.line(surf, theme.BORDER, a, b, thin)
                if not tint:
                    continue
                o1, o2 = w.owner.get(p), w.owner.get(q)
                if o1 == o2:
                    continue
                for owner, (hx, hy) in ((o1, (cx, cy)), (o2, (nx, ny))):
                    if owner is None:
                        continue
                    sh = ((hx - mx) / ln * thick * 0.5, (hy - my) / ln * thick * 0.5)
                    pygame.draw.line(surf, theme.SIDE_FILL[owner],
                                     (a[0] + sh[0], a[1] + sh[1]), (b[0] + sh[0], b[1] + sh[1]), thick)
