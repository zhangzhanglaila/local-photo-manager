"""Global theme management and color definitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QColor, QPalette, QGuiApplication

from ...settings import SettingsManager
from .palette import SIDEBAR_ICON_COLOR_HEX

_LOGGER = logging.getLogger(__name__)


@dataclass
class ThemeColors:
    """Semantic color definitions for a theme."""

    window_background: QColor
    text_primary: QColor
    text_secondary: QColor
    text_disabled: QColor
    accent_color: QColor
    border_color: QColor
    sidebar_background: QColor
    sidebar_text: QColor
    sidebar_section_text: QColor
    sidebar_hover: QColor
    sidebar_selected: QColor
    scrollbar_handle: QColor
    scrollbar_track: QColor

    @property
    def is_dark(self) -> bool:
        return self.window_background.lightness() < 128


LIGHT_THEME = ThemeColors(
    # Apple's neutral surfaces: systemGray6 for the window, hairlines for borders.
    window_background=QColor("#F5F5F7"),
    text_primary=QColor("#1D1D1F"),
    text_secondary=QColor(60, 60, 67, 153),
    text_disabled=QColor(60, 60, 67, 90),
    accent_color=QColor("#007AFF"),
    border_color=QColor(0, 0, 0, 20),
    sidebar_background=QColor("#F0F0F3"),
    sidebar_text=QColor("#1D1D1F"),
    sidebar_section_text=QColor(60, 60, 67, 153),
    sidebar_hover=QColor(0, 0, 0, 12),
    sidebar_selected=QColor(0, 0, 0, 26),
    scrollbar_handle=QColor(0, 0, 0, 66),
    scrollbar_track=QColor(0, 0, 0, 0),
)

# Dark theme colours follow Apple's dark system greys.
DARK_THEME = ThemeColors(
    window_background=QColor("#1C1C1E"),
    text_primary=QColor("#F5F5F7"),
    text_secondary=QColor(235, 235, 245, 153),
    text_disabled=QColor(235, 235, 245, 76),
    accent_color=QColor("#0A84FF"),
    border_color=QColor(255, 255, 255, 26),
    sidebar_background=QColor("#1F1F22"),
    sidebar_text=QColor("#F5F5F7"),
    sidebar_section_text=QColor(235, 235, 245, 153),
    sidebar_hover=QColor(255, 255, 255, 15),
    sidebar_selected=QColor(255, 255, 255, 30),
    scrollbar_handle=QColor(255, 255, 255, 76),
    scrollbar_track=QColor(255, 255, 255, 0),
)


class ThemeManager(QObject):
    """Manages application-wide theme state and updates."""

    themeChanged = Signal(bool)  # Emits is_dark
    """Emitted when the effective theme changes."""

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self._settings = settings
        self._current_colors = LIGHT_THEME
        self._force_dark_mode = False  # Used for Edit Mode override

        # Listen for settings changes
        self._settings.settingsChanged.connect(self._on_settings_changed)

    def _on_settings_changed(self, key: str, value: object) -> None:
        if key == "ui.theme":
            self.apply_theme()

    def get_effective_theme_mode(self) -> str:
        """Return 'light' or 'dark' based on settings and system state."""
        setting = self._settings.get("ui.theme", "system")
        if setting == "dark":
            return "dark"
        if setting == "light":
            return "light"

        # System detection
        app = QGuiApplication.instance()
        if app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
            return "dark"
        return "light"

    def apply_theme(self) -> None:
        """Apply the current theme configuration to the application."""
        mode = self.get_effective_theme_mode()
        is_dark = mode == "dark" or self._force_dark_mode

        if is_dark:
            self._current_colors = DARK_THEME
        else:
            self._current_colors = LIGHT_THEME

        self._apply_palette(self._current_colors)
        # Publish the resolved mode so widgets stop re-deriving it themselves.
        from .theme_mode import set_current_mode

        set_current_mode("dark" if is_dark else "light")
        self.themeChanged.emit(is_dark)

    def set_force_dark(self, enabled: bool) -> None:
        """Force dark mode (e.g., for Edit View)."""
        if self._force_dark_mode == enabled:
            return
        self._force_dark_mode = enabled
        self.apply_theme()

    def current_colors(self) -> ThemeColors:
        return self._current_colors

    def base_colors(self) -> ThemeColors:
        """Return the colors for the effective theme mode, ignoring forced overrides."""
        mode = self.get_effective_theme_mode()
        return DARK_THEME if mode == "dark" else LIGHT_THEME

    def _apply_palette(self, colors: ThemeColors) -> None:
        app = QGuiApplication.instance()
        if not app:
            return

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, colors.window_background)
        palette.setColor(QPalette.ColorRole.WindowText, colors.text_primary)
        palette.setColor(QPalette.ColorRole.Base, colors.window_background)
        palette.setColor(QPalette.ColorRole.AlternateBase, colors.window_background)
        palette.setColor(QPalette.ColorRole.ToolTipBase, colors.window_background)
        palette.setColor(QPalette.ColorRole.ToolTipText, colors.text_primary)
        palette.setColor(QPalette.ColorRole.Text, colors.text_primary)
        palette.setColor(QPalette.ColorRole.Button, colors.window_background)
        palette.setColor(QPalette.ColorRole.ButtonText, colors.text_primary)
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Link, colors.accent_color)
        palette.setColor(QPalette.ColorRole.Highlight, colors.accent_color)
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)

        # Disabled colors
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, colors.text_disabled)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, colors.text_disabled)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, colors.text_disabled)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, colors.text_disabled)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, colors.window_background)

        app.setPalette(palette)


__all__ = ["ThemeManager", "ThemeColors", "LIGHT_THEME", "DARK_THEME"]
