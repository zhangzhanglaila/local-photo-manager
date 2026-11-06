"""One answer to "is the interface dark right now?".

Five widgets each walked their own ladder (window → coordinator → context → theme
manager → settings → QApplication colour scheme → palette lightness), and one of
them only checked the palette. The same dialog could therefore open light when
launched from one entry point and dark from another.

``ThemeManager`` publishes the effective mode here whenever it applies a theme; when
no theme has been applied yet (or the widget lives outside the main window) the
helpers fall back to Qt's colour scheme and finally the widget palette.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QWidget

_current_mode: str | None = None


def set_current_mode(mode: str | None) -> None:
    """Record the mode ``ThemeManager`` resolved ("light", "dark" or "system")."""

    global _current_mode
    _current_mode = mode


def reset_current_mode() -> None:
    """Forget the published mode (used by tests)."""

    set_current_mode(None)


def _mode_from_widget_context(widget: QWidget | None) -> str | None:
    """Ask the hosting window's coordinator for its theme manager, if it has one.

    Widgets inside the main window can reach the live ``ThemeManager``; dialogs and
    detached panels cannot, which is why this lookup lives here once instead of in
    every widget.
    """

    if widget is None:
        return None
    host = widget.window() or widget
    coordinator = getattr(host, "coordinator", None)
    context = getattr(coordinator, "_context", None)
    theme_manager = getattr(context, "theme", None)
    if theme_manager is not None and hasattr(theme_manager, "get_effective_theme_mode"):
        return theme_manager.get_effective_theme_mode()

    settings = getattr(context, "settings", None)
    if settings is not None and hasattr(settings, "get"):
        setting = settings.get("ui.theme", "system")
        if setting in ("dark", "light"):
            return setting
    return None


def resolve_theme_mode(widget: QWidget | None = None) -> str:
    """Return ``"light"`` or ``"dark"`` for *widget*."""

    if _current_mode == "dark":
        return "dark"
    if _current_mode == "light":
        return "light"

    from_widget = _mode_from_widget_context(widget)
    if from_widget in ("dark", "light"):
        return from_widget

    app = QGuiApplication.instance()
    if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
        return "dark"

    palette = widget.palette() if widget is not None else QPalette()
    return "dark" if palette.color(QPalette.ColorRole.Window).lightness() < 128 else "light"


def is_dark(widget: QWidget | None = None) -> bool:
    """Return whether the interface should use dark styling for *widget*."""

    return resolve_theme_mode(widget) == "dark"


__all__ = ["is_dark", "reset_current_mode", "resolve_theme_mode", "set_current_mode"]
