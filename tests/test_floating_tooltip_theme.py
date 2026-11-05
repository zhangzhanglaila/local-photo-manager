"""The floating tooltip must follow the active theme.

Its colours used to be resolved once in ``__init__`` — before the runtime applies
the theme — so the popup kept a dark background with dark text and appeared as an
unreadable black box when hovering a button.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.custom_tooltip import FloatingToolTip


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app
    app.setPalette(QPalette())


def _apply_tooltip_palette(app: QApplication, base: str, text: str) -> None:
    palette = QPalette(app.palette())
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(base))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(text))
    app.setPalette(palette)


def test_unreadable_palette_falls_back_to_legible_colours(qapp: QApplication) -> None:
    # Dark background with dark text is exactly the "black box" state.
    _apply_tooltip_palette(qapp, "#1C1C1E", "#2C2C2E")
    tip = FloatingToolTip()

    contrast = abs(
        tip._background_colour.lightness() - tip._text_colour.lightness()
    )
    assert contrast >= 60, "tooltip colours must stay legible"


def test_colours_follow_theme_changes(qapp: QApplication) -> None:
    _apply_tooltip_palette(qapp, "#1C1C1E", "#F5F5F7")
    tip = FloatingToolTip()
    assert tip._background_colour.lightness() < 128

    _apply_tooltip_palette(qapp, "#F5F5F7", "#1D1D1F")
    tip._refresh_colours()

    assert tip._background_colour.lightness() > 128
    assert tip._text_colour.lightness() < 128
