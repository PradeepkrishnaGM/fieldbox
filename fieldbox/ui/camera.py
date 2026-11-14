"""Pan/zoom camera and hex <-> pixel maths (PLAN.md §5.3). Pointy-top hexes."""

from __future__ import annotations

import math
from typing import Tuple

SQ3 = math.sqrt(3.0)
Coord = Tuple[int, int]


def hex_to_world(c: Coord, s: float) -> Tuple[float, float]:
    """Hex centre in world pixels at zoom 1."""
    return s * SQ3 * (c[0] + c[1] / 2.0), s * 1.5 * c[1]


class Camera:
    """screen = world * zoom + offset. Zoom is always a multiple of `step`, so
    the renderer can cache one surface per zoom value."""

    def __init__(self, s: float, zmin: float, zmax: float, step: float = 0.05) -> None:
        self.s, self.zmin, self.zmax, self.step = s, zmin, zmax, step
        self.zoom = 1.0
        self.ox = 0.0
        self.oy = 0.0

    def _snap(self, z: float) -> float:
        z = round(z / self.step) * self.step
        return round(min(self.zmax, max(self.zmin, z)), 4)

    def hex_to_screen(self, c: Coord) -> Tuple[float, float]:
        x, y = hex_to_world(c, self.s)
        return x * self.zoom + self.ox, y * self.zoom + self.oy

    def screen_to_hex(self, sx: float, sy: float) -> Coord:
        x = (sx - self.ox) / self.zoom
        y = (sy - self.oy) / self.zoom
        q = (SQ3 / 3.0 * x - y / 3.0) / self.s
        r = (2.0 / 3.0 * y) / self.s
        # cube rounding
        cx, cz = q, r
        cy = -cx - cz
        rx, ry, rz = round(cx), round(cy), round(cz)
        dx, dy, dz = abs(rx - cx), abs(ry - cy), abs(rz - cz)
        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy > dz:
            ry = -rx - rz
        else:
            rz = -rx - ry
        return int(rx), int(rz)

    def pan(self, dx: float, dy: float) -> None:
        self.ox += dx
        self.oy += dy

    def zoom_by(self, steps: int, anchor: Tuple[float, float]) -> None:
        """Zoom by wheel notches, keeping the world point under `anchor` fixed."""
        new = self._snap(self.zoom * (1.15 ** steps))
        if new == self.zoom:
            new = self._snap(self.zoom + self.step * (1 if steps > 0 else -1))
        if new == self.zoom:
            return
        ax, ay = anchor
        k = new / self.zoom
        self.ox = ax - (ax - self.ox) * k
        self.oy = ay - (ay - self.oy) * k
        self.zoom = new

    def fit(self, bbox: Tuple[float, float, float, float], view: Tuple[int, int, int, int]) -> None:
        """Centre the world-space box (minx, miny, maxx, maxy; zoom 1) in the
        view rect (x, y, w, h) at the largest zoom that fits."""
        minx, miny, maxx, maxy = bbox
        vx, vy, vw, vh = view
        z = min(vw / (maxx - minx), vh / (maxy - miny)) * 0.97
        z = math.floor(z / self.step) * self.step
        self.zoom = round(min(self.zmax, max(self.zmin, z)), 4)
        self.ox = vx + (vw - (maxx - minx) * self.zoom) / 2 - minx * self.zoom
        self.oy = vy + (vh - (maxy - miny) * self.zoom) / 2 - miny * self.zoom
