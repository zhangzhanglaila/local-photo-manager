"""Shared colour tokens for the application's custom dialogs.

The message, input and progress cards must look like one family; keeping the
light/dark values in a single place stops them drifting apart.
"""

from __future__ import annotations

from PySide6.QtGui import QPalette

ACCENT_LIGHT = "#7B5E3B"
ACCENT_DARK = "#C9A86C"
DANGER = "#CD4949"


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def dialog_tokens(palette: QPalette) -> dict[str, str]:
    """Return the colour tokens used by the custom dialog cards."""

    if is_dark(palette):
        return {
            "shell": "#1C1C1E",
            "text": "#F5F5F7",
            "muted": "#B4B4B4",
            "divider": "rgba(255, 255, 255, 26)",
            "field": "#2C2C2E",
            "secondary": "rgba(255, 255, 255, 18)",
            "hover": "rgba(255, 255, 255, 24)",
            "accent": ACCENT_DARK,
            "on_accent": "#1C1C1E",
        }
    return {
        "shell": "#FFFFFF",
        "text": "#0D0D0D",
        "muted": "#6E6E80",
        "divider": "rgba(0, 0, 0, 0.08)",
        "field": "#F2F2F4",
        "secondary": "rgba(0, 0, 0, 0.04)",
        "hover": "rgba(0, 0, 0, 0.06)",
        "accent": ACCENT_LIGHT,
        "on_accent": "#FFFFFF",
    }


__all__ = [
    "ACCENT_DARK",
    "ACCENT_LIGHT",
    "DANGER",
    "dialog_tokens",
    "is_dark",
]
