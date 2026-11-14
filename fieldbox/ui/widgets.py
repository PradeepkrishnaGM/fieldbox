"""Minimal widgets: Panel, Label, Button (enough for M2)."""

from __future__ import annotations

from typing import Callable, Optional, Tuple

import pygame

from . import theme


class Panel:
    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = rect

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, theme.PANEL, self.rect)
        pygame.draw.rect(surf, theme.PANEL_BORDER, self.rect, 1)


class Label:
    def __init__(self, text: str, size: int = 15, color=theme.TEXT, bold: bool = False) -> None:
        self.text, self.size, self.color, self.bold = text, size, color, bold

    def draw(self, surf: pygame.Surface, pos: Tuple[int, int]) -> int:
        """Draw at pos; returns the line height so callers can stack labels."""
        img = theme.font(self.size, self.bold).render(self.text, True, self.color)
        surf.blit(img, pos)
        return img.get_height()


class Button:
    def __init__(self, rect: pygame.Rect, text: str, on_click: Callable[[], None]) -> None:
        self.rect, self.text, self.on_click = rect, text, on_click
        self.hover = False

    def handle_event(self, e: pygame.event.Event) -> bool:
        if e.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(e.pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.rect.collidepoint(e.pos):
            self.on_click()
            return True
        return False

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, theme.PANEL_BORDER if self.hover else theme.PANEL, self.rect)
        pygame.draw.rect(surf, theme.ACCENT, self.rect, 1)
        img = theme.font(15).render(self.text, True, theme.TEXT)
        surf.blit(img, img.get_rect(center=self.rect.center))
