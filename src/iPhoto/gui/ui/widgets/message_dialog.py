"""Application-styled message dialog.

Native ``QMessageBox`` windows ignore the application chrome: they bring their
own title bar, font and button styling, which is what made confirmations look
like a different program sitting on top of the rounded window. This dialog is the
single replacement: a rounded card with the app's title row, message text and
pill buttons, light and dark aware.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon

_ACCENT_LIGHT = "#7B5E3B"
_ACCENT_DARK = "#C9A86C"
_DANGER = "#CD4949"


class MessageDialog(QDialog):
    """Modal card that presents a message and returns the chosen button key.

    ``buttons`` is an ordered list of ``(label, key)`` pairs; the last one is the
    primary action (filled, accent coloured). Closing the dialog or pressing
    Escape returns ``"cancel"``.
    """

    DEFAULT_WIDTH = 460

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str,
        message: str,
        buttons: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
        primary_role: str = "default",
        checkbox_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        if not buttons:
            buttons = [(tr("dialog.ok"), "ok")]
        self.result_key = "cancel"
        self.checkbox_checked = False
        self._buttons = list(buttons)
        self._primary_role = primary_role

        self.setObjectName("messageDialog")
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(self.DEFAULT_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._shell = QWidget(self)
        self._shell.setObjectName("messageShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

        layout = QVBoxLayout(self._shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self._shell)
        header.setObjectName("messageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 14, 0)
        header_layout.setSpacing(8)

        self._title_label = QLabel(title, header)
        self._title_label.setObjectName("messageTitle")
        self._title_label.setWordWrap(True)
        header_layout.addWidget(self._title_label, 1)

        self._close_button = QToolButton(header)
        self._close_button.setObjectName("messageCloseButton")
        self._close_button.setFixedSize(30, 30)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setToolTip("Close")
        self._close_button.clicked.connect(self.reject)
        header_layout.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        self._message_label = QLabel(message, self._shell)
        self._message_label.setObjectName("messageText")
        self._message_label.setWordWrap(True)
        self._message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._message_label.setContentsMargins(24, 10, 24, 0)
        layout.addWidget(self._message_label)

        if checkbox_text:
            self._checkbox = QCheckBox(checkbox_text, self._shell)
            self._checkbox.setObjectName("messageCheckBox")
            self._checkbox.setContentsMargins(24, 14, 24, 0)
            self._checkbox.toggled.connect(self._on_checkbox_toggled)
            layout.addWidget(self._checkbox)

        actions = QWidget(self._shell)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(24, 20, 24, 20)
        actions_layout.setSpacing(8)
        actions_layout.addStretch(1)
        self._button_widgets: list[QPushButton] = []
        for index, (label, key) in enumerate(self._buttons):
            primary = index == len(self._buttons) - 1
            button = QPushButton(label, actions)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(32)
            button.setObjectName(
                "messagePrimaryButton"
                if primary and self._primary_role == "default"
                else "messageDangerButton"
                if primary
                else "messageSecondaryButton"
            )
            button.clicked.connect(lambda _checked=False, k=key: self._choose(k))
            actions_layout.addWidget(button)
            self._button_widgets.append(button)
        layout.addWidget(actions)

        self._apply_close_icon()
        self.setStyleSheet(self._stylesheet())

    # ------------------------------------------------------------------
    def _on_checkbox_toggled(self, checked: bool) -> None:
        self.checkbox_checked = bool(checked)

    def _choose(self, key: str) -> None:
        self.result_key = key
        self.accept()

    def choose(self, key: str) -> None:
        """Select *key* programmatically (used by tests and callers)."""

        self._choose(key)

    def title(self) -> str:
        return self.windowTitle()

    def message(self) -> str:
        return self._message_label.text()

    def button(self, key: str) -> QPushButton | None:
        """Return the button that carries *key*."""

        for widget, (_label, candidate) in zip(self._button_widgets, self._buttons):
            if candidate == key:
                return widget
        return None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.result_key = "cancel"
            self.reject()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.window().frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )

    def _apply_close_icon(self) -> None:
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(160)
        self._close_button.setIcon(
            load_icon(
                "window.close.svg",
                color=colour.name(QColor.NameFormat.HexArgb),
                size=(12, 12),
            )
        )
        self._close_button.setIconSize(QSize(12, 12))

    # ------------------------------------------------------------------
    def _stylesheet(self) -> str:
        palette = self.palette()
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        if dark:
            shell = "#1C1C1E"
            text, muted = "#F5F5F7", "#B4B4B4"
            divider = "rgba(255, 255, 255, 26)"
            secondary_bg = "rgba(255, 255, 255, 18)"
            hover = "rgba(255, 255, 255, 24)"
            accent, on_accent = _ACCENT_DARK, "#1C1C1E"
        else:
            shell = "#FFFFFF"
            text, muted = "#0D0D0D", "#6E6E80"
            divider = "rgba(0, 0, 0, 0.08)"
            secondary_bg = "rgba(0, 0, 0, 0.04)"
            hover = "rgba(0, 0, 0, 0.06)"
            accent, on_accent = _ACCENT_LIGHT, "#FFFFFF"

        return (
            "QDialog#messageDialog { background: transparent; }"
            f"QWidget#messageShell {{ background-color: {shell}; border-radius: 14px;"
            f"  border: 1px solid {divider}; }}"
            f"QWidget#messageHeader {{ background: transparent; }}"
            f"QLabel#messageTitle {{ font-size: 16px; font-weight: 600; color: {text}; }}"
            f"QLabel#messageText {{ font-size: 13px; color: {muted}; }}"
            f"QToolButton#messageCloseButton {{ border: none; border-radius: 8px;"
            "  background: transparent; }"
            f"QToolButton#messageCloseButton:hover {{ background-color: {hover}; }}"
            f"QPushButton#messageSecondaryButton {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: 1px solid {divider};"
            f"  background-color: {secondary_bg}; color: {text}; font-size: 13px; }}"
            f"QPushButton#messageSecondaryButton:hover {{ background-color: {hover}; }}"
            f"QPushButton#messagePrimaryButton {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: none; background-color: {accent};"
            f"  color: {on_accent}; font-size: 13px; font-weight: 600; }}"
            f"QCheckBox#messageCheckBox {{ font-size: 12px; color: {muted}; }}"
            "QCheckBox#messageCheckBox::indicator { width: 16px; height: 16px; }"
            f"QPushButton#messageDangerButton {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: none; background-color: {_DANGER};"
            "  color: #FFFFFF; font-size: 13px; font-weight: 600; }"
        )


__all__ = ["MessageDialog"]
