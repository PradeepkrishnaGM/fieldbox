"""Procedural unit counters and structure icons. No assets."""

from __future__ import annotations

import math
from typing import Optional, Tuple

import pygame

from ..sim.units import Side
from . import theme

UNIT_KINDS = ("INFANTRY", "TANK", "IFV", "RECON", "ARTILLERY", "SAM",
              "ENGINEER", "HELICOPTER", "JET", "SHIP")
STRUCTURE_KINDS = ("HQ", "INDUSTRY", "POWER", "DATACENTER", "RADAR",
                   "AIRFIELD", "PORT", "FOB")
AIR = ("HELICOPTER", "JET")
SEA = ("SHIP",)
INK = theme.BG                      # glyph colour on unit counters


def strength_colour(strength: float):
    return theme.GOOD if strength > 60 else theme.WARN if strength > 30 else theme.BAD


# ---------------------------------------------------------------------------
# Unit counters
# ---------------------------------------------------------------------------


def _device(surf, kind: str, cx: float, cy: float, iw: float, ih: float, lw: int) -> None:
    x0, y0, x1, y1 = cx - iw / 2, cy - ih / 2, cx + iw / 2, cy + ih / 2
    d = pygame.draw
    if kind in ("INFANTRY", "IFV"):
        d.line(surf, INK, (x0, y0), (x1, y1), lw)
        d.line(surf, INK, (x0, y1), (x1, y0), lw)
    if kind in ("TANK", "IFV"):
        d.ellipse(surf, INK, pygame.Rect(x0, cy - ih * .3, iw, ih * .6), lw)
    elif kind == "RECON":
        d.line(surf, INK, (x0, y1), (x1, y0), lw)
    elif kind == "ARTILLERY":
        d.circle(surf, INK, (round(cx), round(cy)), max(2, round(min(iw, ih) * .3)))
    elif kind == "SAM":
        d.arc(surf, INK, pygame.Rect(x0, y0, iw, ih * 1.6), 0, math.pi, lw)
        d.line(surf, INK, (x0, y0 + ih * .8), (x1, y0 + ih * .8), lw)
    elif kind == "ENGINEER":
        d.line(surf, INK, (x0 + iw * .2, y0), (x0 + iw * .2, y1), lw)
        for yy in (y0, cy, y1):
            d.line(surf, INK, (x0 + iw * .2, yy), (x1 - iw * .1, yy), lw)
    elif kind == "HELICOPTER":
        d.polygon(surf, INK, [(x0, y0), (x0, y1), (cx, cy)])
        d.polygon(surf, INK, [(x1, y0), (x1, y1), (cx, cy)])
    elif kind == "JET":
        d.lines(surf, INK, False, [(x0, y1), (cx, y0), (x1, y1)], lw)
    elif kind == "SHIP":
        d.polygon(surf, INK, [(x0, cy), (x1, cy), (x1 - iw * .2, y1), (x0 + iw * .2, y1)], lw)


def draw_counter(surf: pygame.Surface, centre: Tuple[float, float], s: float, side: Side,
                 kind: str, strength: float = 100.0, out_of_supply: bool = False,
                 ghost_age: Optional[int] = None) -> None:
    """APP-6 style counter. `s` is the hex size in pixels."""
    w = theme.COUNTER_SCALE * s
    h = w / 1.5
    pad = int(w) + 6
    ghost = ghost_age is not None
    target = pygame.Surface((pad * 2, pad * 2), pygame.SRCALPHA) if ghost else surf
    cx, cy = (pad, pad) if ghost else centre
    fill, edge = theme.SIDE_LIGHT[side], theme.SIDE_FILL[side]
    lw = max(1, round(w / 14))
    d = pygame.draw
    air, sea = kind in AIR, kind in SEA
    # Frame: BLUE rectangle (circle at sea), RED diamond (bottom bar at sea).
    if side is Side.BLUE:
        if sea:
            frame = pygame.Rect(cx - h * .75, cy - h * .75, h * 1.5, h * 1.5)
            d.ellipse(target, fill, frame)
            d.ellipse(target, edge, frame, lw + 1)
        else:
            frame = pygame.Rect(cx - w / 2, cy - h / 2, w, h)
            d.rect(target, fill, frame)
            d.rect(target, edge, frame, lw + 1)
    else:
        hw, hh = w * .62, h * .85
        pts = [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]
        d.polygon(target, fill, pts)
        d.polygon(target, edge, pts, lw + 1)
        if sea:
            d.line(target, edge, (cx - hw * .6, cy + hh * 1.12), (cx + hw * .6, cy + hh * 1.12), lw + 1)
    top = cy - (h * .75 if (sea and side is Side.BLUE) else (h / 2 if side is Side.BLUE else h * .85))
    if air:                                         # arc cap above the frame
        d.arc(target, edge, pygame.Rect(cx - w * .3, top - h * .3, w * .6, h * .6), 0.15, math.pi - 0.15, lw + 1)
    _device(target, kind, cx, cy, w * .5, h * .55, lw)
    # Strength bar under the frame, and a "!" when cut off.
    bar_y = cy + (h * .85 if side is Side.RED else h / 2) + max(2, lw + 1) + (h * .2 if sea else 0)
    bw = w * strength / 100.0
    d.rect(target, theme.BG, pygame.Rect(cx - w / 2 - 1, bar_y - 1, w + 2, max(3, round(w / 9)) + 2))
    d.rect(target, strength_colour(strength), pygame.Rect(cx - w / 2, bar_y, bw, max(3, round(w / 9))))
    if out_of_supply:
        f = theme.font(max(10, round(w * .6)), bold=True)
        img = f.render("!", True, theme.BAD)
        target.blit(img, (cx + w / 2 - 2, cy - h / 2 - img.get_height() * .6))
    if ghost:
        f = theme.font(max(10, round(w * .5)))
        target.blit(f.render(str(ghost_age), True, theme.TEXT), (cx + w / 2 + 1, cy - h / 2 - 2))
        target.set_alpha(102)                       # 40 %
        surf.blit(target, (centre[0] - pad, centre[1] - pad))


# ---------------------------------------------------------------------------
# Structure icons
# ---------------------------------------------------------------------------


def _glyph(surf, kind: str, cx: float, cy: float, a: float, col, lw: int) -> None:
    """Draw inside a box of half-size `a` around (cx, cy)."""
    d = pygame.draw
    L, R, T, B = cx - a, cx + a, cy - a, cy + a
    if kind == "HQ":                                          # flag on a pole
        d.line(surf, col, (L + a * .3, T), (L + a * .3, B), lw)
        d.polygon(surf, col, [(L + a * .3, T), (R, T + a * .45), (L + a * .3, T + a * .9)])
    elif kind == "INDUSTRY":                                  # factory roofline
        d.polygon(surf, col, [(L, B), (L, cy - a * .2), (L + a * .6, cy - a * .7), (L + a * .6, cy - a * .2),
                              (cx + a * .2, cy - a * .7), (cx + a * .2, cy - a * .2),
                              (R, cy - a * .7), (R, B)])
    elif kind == "POWER":                                     # lightning bolt
        d.polygon(surf, col, [(cx + a * .2, T), (L + a * .2, cy + a * .15), (cx - a * .05, cy + a * .15),
                              (cx - a * .2, B), (R - a * .2, cy - a * .2), (cx + a * .05, cy - a * .2)])
    elif kind == "DATACENTER":                                # three server slots
        for i in range(3):
            y = T + a * .15 + i * a * .7
            d.rect(surf, col, pygame.Rect(L, y, 2 * a, a * .5), lw)
            d.circle(surf, col, (round(R - a * .3), round(y + a * .25)), max(1, lw))
    elif kind == "RADAR":                                     # dish on a stand
        d.arc(surf, col, pygame.Rect(L, T, 2 * a, 2 * a), math.pi * 1.05, math.pi * 1.95, lw + 1)
        d.line(surf, col, (cx, cy), (cx, B), lw)
        d.line(surf, col, (cx - a * .5, B), (cx + a * .5, B), lw)
        d.line(surf, col, (cx, cy - a * .5), (R - a * .1, T + a * .1), lw)
    elif kind == "AIRFIELD":                                  # runway bar with dashes
        d.rect(surf, col, pygame.Rect(L, cy - a * .35, 2 * a, a * .7))
        for i in range(3):
            d.line(surf, INK, (L + a * (.2 + i * .65), cy), (L + a * (.55 + i * .65), cy), lw)
    elif kind == "PORT":                                      # anchor
        d.circle(surf, col, (round(cx), round(T + a * .25)), max(2, round(a * .22)), lw)
        d.line(surf, col, (cx, T + a * .45), (cx, B - a * .1), lw)
        d.line(surf, col, (cx - a * .5, T + a * .8), (cx + a * .5, T + a * .8), lw)
        d.arc(surf, col, pygame.Rect(L, cy - a * .2, 2 * a, 1.4 * a), math.pi, 2 * math.pi, lw)
    elif kind == "FOB":                                       # tent
        d.polygon(surf, col, [(L, B), (cx, T), (R, B)])
        d.polygon(surf, INK, [(cx - a * .25, B), (cx, cy), (cx + a * .25, B)])


def draw_structure(surf: pygame.Surface, centre: Tuple[float, float], s: float,
                   side: Optional[Side], kind: str, strength: float = 100.0,
                   ring: bool = False) -> None:
    """Square icon with a side-coloured border. `s` is the hex size in pixels."""
    side_len = theme.STRUCTURE_SCALE * s
    cx, cy = centre
    rect = pygame.Rect(cx - side_len / 2, cy - side_len / 2, side_len, side_len)
    edge = theme.SIDE_FILL[side] if side is not None else theme.NEUTRAL
    lw = max(1, round(side_len / 10))
    pygame.draw.rect(surf, theme.PANEL, rect)
    pygame.draw.rect(surf, edge, rect, lw + 1)
    _glyph(surf, kind, cx, cy, side_len * .3, theme.TEXT, max(1, lw))
    if ring:
        r = pygame.Rect(0, 0, side_len * 1.5, side_len * 1.5)
        r.center = (round(cx), round(cy))
        pygame.draw.arc(surf, strength_colour(strength), r, math.pi / 2,
                        math.pi / 2 + 2 * math.pi * strength / 100.0, max(2, lw))
