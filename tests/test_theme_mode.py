"""Dark-mode detection has one owner.

Five widgets each walked their own ladder (window → coordinator → context → theme
manager → settings → Qt colour scheme → palette), and one only checked the palette.
The same dialog could open light from one entry point and dark from another.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.theme_mode import (
    is_dark,
    reset_current_mode,
    resolve_theme_mode,
    set_current_mode,
)


class _ThemeManager:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    def get_effective_theme_mode(self) -> str:
        return self._mode


class _Coordinator:
    def __init__(self, theme: _ThemeManager | None) -> None:
        self._context = type("_Ctx", (), {"theme": theme, "settings": None})()


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    reset_current_mode()
    yield app
    reset_current_mode()


def _widget_with_coordinator(qapp: QApplication, theme: _ThemeManager | None) -> QWidget:
    widget = QWidget()
    widget.coordinator = _Coordinator(theme)  # type: ignore[attr-defined]
    return widget


def test_published_mode_wins(qapp: QApplication) -> None:
    set_current_mode("dark")

    assert resolve_theme_mode(None) == "dark"
    assert is_dark(None) is True


def test_widget_context_theme_is_consulted(qapp: QApplication) -> None:
    widget = _widget_with_coordinator(qapp, _ThemeManager("dark"))

    assert is_dark(widget) is True


def test_palette_is_the_last_resort(qapp: QApplication) -> None:
    widget = QWidget()
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#101010"))
    widget.setPalette(palette)

    assert is_dark(widget) is True


def test_light_palette_reports_light(qapp: QApplication) -> None:
    widget = QWidget()
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#F5F5F7"))
    widget.setPalette(palette)

    assert is_dark(widget) is False
