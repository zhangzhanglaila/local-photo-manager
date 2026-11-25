"""Custom title bar widget hosting the caption buttons.

The window is frameless, so the title bar builds its own minimise / maximise /
close controls. They deliberately follow the reference application's caption
buttons — a wide, flat target with a small monochrome glyph — instead of the
macOS traffic lights this used to draw: the traffic lights are a macOS idiom and
read as a foreign element on the Windows/Linux chrome the rest of the window
uses.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from .main_window_metrics import (
    HEADER_BUTTON_SIZE,
    HEADER_ICON_GLYPH_SIZE,
    TITLE_BAR_HEIGHT,
    TITLE_CONTROL_BUTTON_RADIUS,
    TITLE_CONTROL_BUTTON_SIZE,
    TITLE_CONTROL_CLOSE_HOVER_BACKGROUND,
    TITLE_CONTROL_GLYPH_SIZE,
)
from ..icon import load_icon


class CaptionButton(QToolButton):
    """Flat caption button whose glyph brightens on hover.

    ``QIcon`` bakes its colour in, so the hover state swaps in a second, brighter
    icon rather than relying on a stylesheet (which cannot recolour an icon).
    """

    def __init__(self, parent: QWidget | None = None, *, danger: bool = False) -> None:
        super().__init__(parent)
        self._danger = danger
        self._icon_name = ""
        self._normal_icon = QIcon()
        self._hover_icon = QIcon()
        self.setFixedSize(TITLE_CONTROL_BUTTON_SIZE)
        self.setIconSize(TITLE_CONTROL_GLYPH_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setAutoRaise(False)
        self.setStyleSheet(
            "QToolButton {"
            "  border: none;"
            f"  border-radius: {TITLE_CONTROL_BUTTON_RADIUS}px;"
            "  background: transparent;"
            "}"
            "QToolButton:hover {"
            f"  background-color: {TITLE_CONTROL_CLOSE_HOVER_BACKGROUND if danger else 'rgba(0, 0, 0, 0.07)'};"
            "}"
            "QToolButton:pressed {"
            f"  background-color: {TITLE_CONTROL_CLOSE_HOVER_BACKGROUND if danger else 'rgba(0, 0, 0, 0.12)'};"
            "}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def configure(self, icon_name: str, tooltip: str) -> None:
        """Set the glyph and tooltip, tinting both hover states for the theme."""

        self._icon_name = icon_name
        self.setToolTip(tooltip)
        self._refresh_icons()

    def set_glyph(self, icon_name: str) -> None:
        """Swap the glyph without touching the tooltip (maximise/restore)."""

        self._icon_name = icon_name
        self._refresh_icons()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._hover_icon.isNull():
            self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._normal_icon.isNull():
            self.setIcon(self._normal_icon)
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.PaletteChange:
            self._refresh_icons()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _refresh_icons(self) -> None:
        if not self._icon_name:
            return
        palette = self.palette()
        base = QColor(palette.color(QPalette.ColorRole.WindowText))
        normal = QColor(base)
        normal.setAlpha(150)
        hover = QColor(base)
        if self._danger:
            hover = QColor("#FFFFFF")
        self._normal_icon = load_icon(
            self._icon_name, color=normal.name(QColor.NameFormat.HexArgb)
        )
        self._hover_icon = load_icon(
            self._icon_name, color=hover.name(QColor.NameFormat.HexArgb)
        )
        self.setIcon(self._hover_icon if self.underMouse() else self._normal_icon)


class CustomTitleBar(QWidget):
    """Compact title bar hosting the window label and caption controls."""

    def __init__(self, parent: QWidget | None = None, window_title: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("windowTitleBar")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        self.window_controls = QWidget(self)
        self.window_controls.setObjectName("windowControls")
        self.window_controls.setFixedHeight(TITLE_CONTROL_BUTTON_SIZE.height())
        self.window_controls.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

        controls_layout = QHBoxLayout(self.window_controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(2)

        self.minimize_button = CaptionButton(self.window_controls)
        self.maximize_button = CaptionButton(self.window_controls)
        self.close_button = CaptionButton(self.window_controls, danger=True)

        # Order used by the reference title bar: minimise, maximise, close.
        self.minimize_button.configure("window.minimize.svg", "Minimize")
        self.maximize_button.configure("window.maximize.svg", "Maximize")
        self.close_button.configure("window.close.svg", "Close")

        # ``window_manager`` and the UI contract still address the middle button
        # by its historical name; it now owns maximise/restore.
        self.fullscreen_button = self.maximize_button

        for button in (
            self.minimize_button,
            self.maximize_button,
            self.close_button,
        ):
            controls_layout.addWidget(button)

        layout.addWidget(self.window_controls, 0, Qt.AlignmentFlag.AlignVCenter)

        self.window_title_label = QLabel(window_title, self)
        self.window_title_label.setObjectName("windowTitleLabel")
        self.window_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.window_title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.window_title_label, 1)

        # Keeps the centred title visually centred despite the caption buttons.
        spacer = QWidget(self)
        spacer.setFixedWidth(self.window_controls.sizeHint().width())
        spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(spacer, 0)

    def configure_header_button(self, button: QToolButton, icon_name: str, tooltip: str) -> None:
        """Normalize header button appearance to the design defaults.

        The helper mirrors the behaviour from the original ``Ui_MainWindow`` implementation,
        keeping the glyph dimensions and hover behaviour consistent for any caller that needs
        to style an action button within the window chrome.
        """

        button.setIcon(load_icon(icon_name))
        button.setIconSize(HEADER_ICON_GLYPH_SIZE)
        button.setFixedSize(HEADER_BUTTON_SIZE)
        button.setAutoRaise(True)


__all__ = ["CaptionButton", "CustomTitleBar"]
