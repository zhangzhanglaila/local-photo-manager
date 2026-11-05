"""Custom title bar widget implementing the macOS-style window chrome."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from .main_window_metrics import (
    HEADER_BUTTON_SIZE,
    HEADER_ICON_GLYPH_SIZE,
    TITLE_BAR_HEIGHT,
    WINDOW_CONTROL_BUTTON_SIZE,
    WINDOW_CONTROL_GLYPH_SIZE,
)
from ..icon import load_icon


class CustomTitleBar(QWidget):
    """Compact title bar hosting the window label and traffic light controls."""

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
        self.window_controls.setFixedHeight(WINDOW_CONTROL_BUTTON_SIZE.height())
        self.window_controls.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

        controls_layout = QHBoxLayout(self.window_controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.minimize_button = QToolButton(self.window_controls)
        self.fullscreen_button = QToolButton(self.window_controls)
        self.close_button = QToolButton(self.window_controls)

        # macOS order, left to right: close, minimise, zoom.
        for button, icon_name, tooltip in (
            (self.close_button, "red.close.circle.svg", "Close"),
            (self.minimize_button, "yellow.minimum.circle.svg", "Minimize"),
            (self.fullscreen_button, "green.maximum.circle.svg", "Enter Full Screen"),
        ):
            self._configure_window_control_button(button, icon_name, tooltip)
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

        # Keeps the centred title visually centred despite the traffic lights.
        spacer = QWidget(self)
        spacer.setFixedWidth(
            self.window_controls.sizeHint().width()
        )
        spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(spacer, 0)

    def _configure_window_control_button(
        self,
        button: QToolButton,
        icon_name: str,
        tooltip: str,
    ) -> None:
        """Apply the shared styling for the custom window control buttons."""

        button.setIcon(load_icon(icon_name))
        button.setIconSize(WINDOW_CONTROL_GLYPH_SIZE)
        button.setFixedSize(WINDOW_CONTROL_BUTTON_SIZE)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(tooltip)

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


__all__ = ["CustomTitleBar"]
