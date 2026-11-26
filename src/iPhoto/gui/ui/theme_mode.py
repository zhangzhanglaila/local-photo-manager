"""One answer to "is the interface dark right now?".

Five widgets each walked their own ladder (window → coordinator → context → theme
manager → settings → QApplication colour scheme → palette lightness), and one of
them only checked the palette. The same dialog could therefore open light when
launched from one entry point and dark from another.

``ThemeManager`` owns the applied mode (including the forced dark override used
by the edit view). The helpers below ask it through the widget's host window —
walking the parent chain, because a frameless dialog is its own top-level window
— and fall back to Qt's colour scheme and finally the widget palette. There is no
module-level "current mode" any more: two windows cannot disagree about it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QWidget


def _theme_manager_from_widget(widget: QWidget | None):
    """Return the live ``ThemeManager`` hosting *widget*, if one is reachable."""

    candidate = widget
    depth = 0
    while candidate is not None and depth < 16:
        coordinator = getattr(candidate, "coordinator", None)
        context = getattr(coordinator, "_context", None)
        if context is None:
            context = getattr(candidate, "_context", None)
        if context is not None:
            theme_manager = getattr(context, "theme", None)
            if theme_manager is not None:
                return theme_manager, context
        candidate = candidate.parentWidget()
        depth += 1
    return None, None


def _mode_from_widget_context(widget: QWidget | None) -> str | None:
    """Return the mode the hosting window resolved, if a window is reachable.

    Widgets inside the main window can reach the live ``ThemeManager``; dialogs and
    detached panels reach it through their parent chain. This lookup lives here once
    instead of in every widget.
    """

    theme_manager, context = _theme_manager_from_widget(widget)
    if theme_manager is not None:
        current = getattr(theme_manager, "current_mode", None)
        if callable(current):
            mode = current()
            if mode in ("dark", "light"):
                return mode
        getter = getattr(theme_manager, "get_effective_theme_mode", None)
        if callable(getter):
            mode = getter()
            if mode in ("dark", "light"):
                return mode

    if context is not None:
        settings = getattr(context, "settings", None)
        if settings is not None and hasattr(settings, "get"):
            setting = settings.get("ui.theme", "system")
            if setting in ("dark", "light"):
                return setting
    return None


def resolve_theme_mode(widget: QWidget | None = None) -> str:
    """Return ``"light"`` or ``"dark"`` for *widget*."""

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


__all__ = ["is_dark", "resolve_theme_mode"]
