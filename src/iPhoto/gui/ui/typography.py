"""Application-wide typography.

The UI mixes sizes and weights, which is fine, but the *family* has to be one:
when a style asks for ``font-weight: 600`` and the current family has no CJK
glyphs, Qt silently substitutes a serif face and that part of the UI looks like
a different application. Picking an explicit CJK-capable family once, at
startup, keeps every label on the same typeface.

Widgets should express hierarchy with :data:`TYPE_SCALE` (or plain size/weight
in their stylesheet) instead of naming a family.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase

#: Preferred families, best first. The first one available wins.
FAMILY_CANDIDATES: tuple[str, ...] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Segoe UI",
)

#: Base point size and the relative steps used across the UI.
BASE_POINT_SIZE = 9

TYPE_SCALE: dict[str, int] = {
    "caption": 8,
    "body": BASE_POINT_SIZE,
    "subtitle": 10,
    "title": 12,
    "heading": 14,
    "display": 22,
}


def resolve_family() -> str:
    """Return the first installed family from :data:`FAMILY_CANDIDATES`."""

    try:
        available = set(QFontDatabase.families())
    except Exception:  # pragma: no cover - font database always exists
        available = set()
    for family in FAMILY_CANDIDATES:
        if family in available:
            return family
    return FAMILY_CANDIDATES[-1]


def app_font() -> QFont:
    """Return the font every widget should inherit."""

    font = QFont(resolve_family(), BASE_POINT_SIZE)
    # Slightly tighter hinting looks better with the CJK faces above.
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    if sys.platform == "win32":
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def apply_app_font(app) -> QFont:
    """Set the application font and return it."""

    font = app_font()
    app.setFont(font)
    return font


__all__ = ["FAMILY_CANDIDATES", "TYPE_SCALE", "app_font", "apply_app_font", "resolve_family"]
