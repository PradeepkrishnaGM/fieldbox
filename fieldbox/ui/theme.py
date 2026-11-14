"""Colours, fonts and sizes. The only place UI colours live."""

from __future__ import annotations

from typing import Dict, Tuple

import pygame

from ..sim.units import Side

Color = Tuple[int, int, int]


def rgb(h: str) -> Color:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def mix(a: Color, b: Color, t: float) -> Color:
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


BG = rgb("14181C")
PANEL = rgb("1E242A")
PANEL_BORDER = rgb("3A444D")
TEXT = rgb("E6E1D3")
DIM = rgb("9AA3A8")
ACCENT = rgb("E0B44C")

SIDE_FILL: Dict[Side, Color] = {Side.BLUE: rgb("4A90D9"), Side.RED: rgb("D9534A")}
SIDE_LIGHT: Dict[Side, Color] = {Side.BLUE: rgb("9CC6F0"), Side.RED: rgb("F0A39C")}
NEUTRAL = rgb("8A8F94")

WATER_DEEP = rgb("1F4E79")
WATER_SHALLOW = rgb("3C7DB5")
TERRAIN: Dict[str, Color] = {
    "PLAIN": rgb("9DB56B"), "FOREST": rgb("4F7A3A"), "HILL": rgb("B59B66"),
    "MOUNTAIN": rgb("8C8C8C"), "URBAN": rgb("7A6F66"),
}
RIVER = rgb("5FA3D9")
ROAD = rgb("D9C9A3")
BORDER = rgb("2A3036")          # thin province border
GOOD, WARN, BAD = rgb("6DBE6D"), rgb("E0B44C"), rgb("D9534A")

TINT = 0.12                     # owned-province tint strength
STRUCTURE_SCALE = 1.1           # structure icon side, in hex sizes (plan says 0.8; 1.1 reads better)
COUNTER_SCALE = 0.9             # unit counter width, in hex sizes

_fonts: Dict[Tuple[int, bool], pygame.font.Font] = {}


def reset_fonts() -> None:
    """Forget cached fonts (they die with pygame.quit())."""
    _fonts.clear()


def font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    f = _fonts.get(key)
    if f is None:
        if not pygame.font.get_init():
            pygame.font.init()
        try:
            f = pygame.font.SysFont("dejavusans,liberationsans,arial", size, bold=bold)
        except Exception:
            f = pygame.font.Font(None, size + 4)
        _fonts[key] = f
    return f
