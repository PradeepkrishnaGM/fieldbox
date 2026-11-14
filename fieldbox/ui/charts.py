"""Small chart drawing helpers for the Evolution Lab (PLAN.md 5.7). Pure pygame, no data logic.

Every function draws a titled panel into `rect` and returns the inner plot
rect, so a screen can hit-test clicks against it (`index_at`).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import pygame

from . import theme

PALETTE = [theme.rgb(h) for h in ("4A90D9", "E0B44C", "6DBE6D", "D9534A", "B07FD0", "5FD0C8", "E08A4C")]
PAD_L, PAD_R, PAD_T, PAD_B = 44, 12, 44, 22


def _panel(surf: pygame.Surface, rect: pygame.Rect, title: str) -> pygame.Rect:
    pygame.draw.rect(surf, theme.PANEL, rect)
    pygame.draw.rect(surf, theme.PANEL_BORDER, rect, 1)
    surf.blit(theme.font(14, True).render(title, True, theme.ACCENT), (rect.x + 8, rect.y + 5))
    return pygame.Rect(rect.x + PAD_L, rect.y + PAD_T, rect.w - PAD_L - PAD_R, rect.h - PAD_T - PAD_B)


def _empty(surf: pygame.Surface, plot: pygame.Rect, text: str = "no data yet") -> None:
    img = theme.font(14).render(text, True, theme.DIM)
    surf.blit(img, img.get_rect(center=plot.center))


def index_at(plot: pygame.Rect, n: int, x: int) -> Optional[int]:
    """Which of `n` evenly spaced x positions (centre-aligned columns) is under pixel x."""
    if n <= 0 or not plot.left <= x <= plot.right:
        return None
    return min(n - 1, int((x - plot.left) / plot.w * n))


def _x_of(plot: pygame.Rect, i: int, n: int) -> int:
    return int(plot.left + (i + 0.5) / max(n, 1) * plot.w)


def _axis(surf, plot, lo: float, hi: float, fmt: str = "{:.2f}", ticks: int = 4) -> None:
    f = theme.font(11)
    for k in range(ticks + 1):
        v = lo + (hi - lo) * k / ticks
        y = int(plot.bottom - plot.h * k / ticks)
        pygame.draw.line(surf, theme.PANEL_BORDER, (plot.left, y), (plot.right, y), 1)
        img = f.render(fmt.format(v), True, theme.DIM)
        surf.blit(img, (plot.left - img.get_width() - 4, y - img.get_height() // 2))


def _xlabels(surf, plot, n: int) -> None:
    f = theme.font(11)
    step = max(1, n // 8)
    for i in range(0, n, step):
        img = f.render(str(i), True, theme.DIM)
        surf.blit(img, (_x_of(plot, i, n) - img.get_width() // 2, plot.bottom + 4))


def _legend(surf, rect, names: Sequence[str], colors: Sequence) -> None:
    f = theme.font(11)
    x = rect.right - 8
    for name, c in reversed(list(zip(names, colors))):
        img = f.render(name, True, theme.TEXT)
        x -= img.get_width()
        surf.blit(img, (x, rect.y + 26))
        x -= 12
        pygame.draw.rect(surf, c, (x, rect.y + 29, 8, 8))
        x -= 8


def line_chart(surf: pygame.Surface, rect: pygame.Rect, title: str,
               series: Dict[str, List[float]], ymin: Optional[float] = None,
               ymax: Optional[float] = None, fmt: str = "{:.2f}",
               dashed: Sequence[str] = (), mark: Optional[int] = None) -> pygame.Rect:
    """Multi-line chart over generation index. `mark` = highlighted x index."""
    plot = _panel(surf, rect, title)
    n = max((len(v) for v in series.values()), default=0)
    if n == 0:
        _empty(surf, plot)
        return plot
    vals = [x for v in series.values() for x in v]
    lo = min(vals) if ymin is None else ymin
    hi = max(vals) if ymax is None else ymax
    if hi - lo < 1e-9:
        hi = lo + 1.0
    _axis(surf, plot, lo, hi, fmt)
    _xlabels(surf, plot, n)
    names, colors = [], []
    for k, (name, v) in enumerate(series.items()):
        c = PALETTE[k % len(PALETTE)]
        names.append(name)
        colors.append(c)
        pts = [(_x_of(plot, i, n), int(plot.bottom - (y - lo) / (hi - lo) * plot.h))
               for i, y in enumerate(v)]
        if name in dashed:                       # constant reference line, drawn dashed
            y0 = pts[0][1] if pts else plot.bottom
            for x in range(plot.left, plot.right, 10):
                pygame.draw.line(surf, c, (x, y0), (min(x + 5, plot.right), y0), 1)
        elif len(pts) > 1:
            pygame.draw.lines(surf, c, False, pts, 2)
        else:
            pygame.draw.circle(surf, c, pts[0], 3)
    if mark is not None and 0 <= mark < n:
        x = _x_of(plot, mark, n)
        pygame.draw.line(surf, theme.ACCENT, (x, plot.top), (x, plot.bottom), 1)
    _legend(surf, rect, names, colors)
    return plot


def heatmap(surf: pygame.Surface, rect: pygame.Rect, title: str, matrix: Optional[List[List[float]]],
            labels: Sequence[str], mark: Optional[int] = None) -> pygame.Rect:
    """Square matrix of 0..1 win rates, row i vs column j (red = loses, green = wins)."""
    plot = _panel(surf, rect, title)
    n = len(matrix) if matrix else 0
    if n == 0:
        _empty(surf, plot, "available when the run finishes")
        return plot
    cell = min(plot.w, plot.h) // n
    ox, oy = plot.left + (plot.w - cell * n) // 2, plot.top + (plot.h - cell * n) // 2
    f = theme.font(11)
    for i in range(n):
        for j in range(n):
            v = matrix[i][j]
            col = theme.mix(theme.BAD, theme.PANEL, v / 0.5) if v < 0.5 else \
                theme.mix(theme.PANEL, theme.GOOD, (v - 0.5) / 0.5)
            r = pygame.Rect(ox + j * cell, oy + i * cell, cell - 1, cell - 1)
            pygame.draw.rect(surf, col, r)
            if cell >= 28 and i != j:
                img = f.render(f"{v * 100:.0f}", True, theme.TEXT)
                surf.blit(img, img.get_rect(center=r.center))
    for i, lab in enumerate(labels[:n]):
        img = f.render(str(lab), True, theme.ACCENT if i == mark else theme.DIM)
        surf.blit(img, (ox - img.get_width() - 4, oy + i * cell + (cell - img.get_height()) // 2))
        surf.blit(img, (ox + i * cell + (cell - img.get_width()) // 2, oy + n * cell + 3))
    return plot


def stacked_bar(surf: pygame.Surface, rect: pygame.Rect, title: str, rows: List[Dict[str, float]],
                keys: Sequence[str], mark: Optional[int] = None) -> pygame.Rect:
    """One column per generation; each column is the share of every key (sums to 1)."""
    plot = _panel(surf, rect, title)
    n = len(rows)
    if n == 0:
        _empty(surf, plot)
        return plot
    _axis(surf, plot, 0.0, 1.0, "{:.0%}", 2)
    _xlabels(surf, plot, n)
    bw = max(2, int(plot.w / n * 0.7))
    for i, row in enumerate(rows):
        total = sum(row.get(k, 0.0) for k in keys) or 1.0
        y = plot.bottom
        x = _x_of(plot, i, n) - bw // 2
        for k, key in enumerate(keys):
            h = int(round(plot.h * row.get(key, 0.0) / total))
            if h > 0:
                pygame.draw.rect(surf, PALETTE[k % len(PALETTE)], (x, y - h, bw, h))
                y -= h
    if mark is not None and 0 <= mark < n:
        pygame.draw.rect(surf, theme.ACCENT, (_x_of(plot, mark, n) - bw // 2 - 1, plot.top,
                                              bw + 2, plot.h), 1)
    _legend(surf, rect, [k.replace("_", " ") for k in keys], PALETTE)
    return plot
