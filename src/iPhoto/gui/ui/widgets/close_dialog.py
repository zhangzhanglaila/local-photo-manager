"""The "close window" prompt.

The window is frameless, so closing it used to fall back to a native
``QMessageBox``. That box ignored every piece of chrome styling the application
uses, and it could not offer the reference application's "do not ask again"
choice. This dialog mirrors the reference card: a rounded sheet with two option
rows (minimise / quit), a remember checkbox and a cancel pill.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
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

_DANGER = "#CD4949"


class _OptionCard(QPushButton):
    """Large clickable card describing one close action."""

    def __init__(
        self,
        title: str,
        description: str,
        icon_name: str,
        *,
        danger: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("closeOptionDanger" if danger else "closeOption")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(78)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        self._icon_tile = QLabel(self)
        self._icon_tile.setObjectName("closeOptionIconDanger" if danger else "closeOptionIcon")
        self._icon_tile.setFixedSize(42, 42)
        self._icon_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_tile.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._icon_tile, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(4)
        title_label = QLabel(title, self)
        title_label.setObjectName("closeOptionTitleDanger" if danger else "closeOptionTitle")
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_column.addWidget(title_label)
        description_label = QLabel(description, self)
        description_label.setObjectName("closeOptionDescription")
        description_label.setWordWrap(True)
        description_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        text_column.addWidget(description_label)
        layout.addLayout(text_column, 1)

        self._icon_name = icon_name
        self._danger = danger
        self._apply_icon()

    def _apply_icon(self) -> None:
        palette = self.palette()
        colour = QColor(_DANGER if self._danger else palette.color(QPalette.ColorRole.WindowText))
        if not self._danger:
            colour.setAlpha(210)
        self._icon_tile.setPixmap(
            load_icon(
                self._icon_name,
                color=colour.name(QColor.NameFormat.HexArgb),
                size=(18, 18),
            ).pixmap(18, 18)
        )


class CloseDialog(QDialog):
    """Modal close prompt returning ``"minimise"``, ``"quit"`` or ``"cancel"``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = "cancel"
        self.remember = False

        self.setObjectName("closeDialog")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        sheet = QWidget(self)
        sheet.setObjectName("closeShell")
        sheet.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(sheet)

        layout = QVBoxLayout(sheet)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header ---------------------------------------------------------
        header = QWidget(sheet)
        header.setObjectName("closeHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(28, 26, 28, 18)
        header_layout.setSpacing(10)

        kicker = QLabel(tr("close.kicker"), header)
        kicker.setObjectName("closeKicker")
        kicker.setFixedHeight(24)
        kicker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kicker.setMaximumWidth(84)
        header_layout.addWidget(kicker, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel(tr("dialog.close_title"), header)
        title.setObjectName("closeTitle")
        header_layout.addWidget(title)

        prompt = QLabel(tr("dialog.close_prompt"), header)
        prompt.setObjectName("closePrompt")
        prompt.setWordWrap(True)
        header_layout.addWidget(prompt)

        layout.addWidget(header)

        # Options --------------------------------------------------------
        body = QWidget(sheet)
        body.setObjectName("closeBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 18, 24, 8)
        body_layout.setSpacing(12)

        self.minimise_card = _OptionCard(
            tr("close.minimise_title"),
            tr("close.minimise_desc"),
            "window.minimize.svg",
            parent=body,
        )
        self.minimise_card.clicked.connect(lambda: self._choose("minimise"))
        body_layout.addWidget(self.minimise_card)

        self.quit_card = _OptionCard(
            tr("close.quit_title"),
            tr("close.quit_desc"),
            "power.svg",
            danger=True,
            parent=body,
        )
        self.quit_card.clicked.connect(lambda: self._choose("quit"))
        body_layout.addWidget(self.quit_card)
        layout.addWidget(body)

        # Remember -------------------------------------------------------
        self.remember_box = QCheckBox(tr("close.remember"), sheet)
        self.remember_box.setObjectName("closeRemember")
        self.remember_box.toggled.connect(self._on_remember_toggled)
        remember_row = QWidget(sheet)
        remember_row.setObjectName("closeRememberRow")
        remember_layout = QHBoxLayout(remember_row)
        remember_layout.setContentsMargins(24, 8, 24, 0)
        remember_layout.addWidget(self.remember_box)
        remember_layout.addStretch(1)
        layout.addWidget(remember_row)

        # Actions --------------------------------------------------------
        actions = QWidget(sheet)
        actions.setObjectName("closeActions")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(24, 16, 24, 22)
        actions_layout.addStretch(1)
        self.cancel_button = QPushButton(tr("dialog.close_cancel"), actions)
        self.cancel_button.setObjectName("closeCancelButton")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        actions_layout.addWidget(self.cancel_button)
        layout.addWidget(actions)

        self.setStyleSheet(self._stylesheet())

    # ------------------------------------------------------------------
    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.remember = self.remember_box.isChecked()
        self.accept()

    def _on_remember_toggled(self, checked: bool) -> None:
        self.remember = checked

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.choice = "cancel"
            self.reject()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _stylesheet(self) -> str:
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        if dark:
            shell, panel = "#1C1C1E", "rgba(255, 255, 255, 12)"
            text, muted = "#F5F5F7", "#B4B4B4"
            divider = "rgba(255, 255, 255, 26)"
            accent_bg, accent_fg = "rgba(201, 168, 108, 22)", "#C9A86C"
            danger_bg = "rgba(205, 73, 73, 34)"
        else:
            shell, panel = "#FFFFFF", "#F7F7F8"
            text, muted = "#0D0D0D", "#6E6E80"
            divider = "rgba(0, 0, 0, 0.08)"
            accent_bg, accent_fg = "rgba(123, 94, 59, 26)", "#7B5E3B"
            danger_bg = "rgba(205, 73, 73, 26)"

        return (
            "QDialog#closeDialog { background: transparent; }"
            f"QWidget#closeShell {{ background-color: {shell}; border-radius: 20px;"
            f"  border: 1px solid {divider}; }}"
            f"QWidget#closeHeader {{ border-bottom: 1px solid {divider}; }}"
            f"QLabel#closeKicker {{ background-color: {accent_bg}; color: {accent_fg};"
            "  border-radius: 12px; font-size: 11px; font-weight: 700; letter-spacing: 1px; }"
            f"QLabel#closeTitle {{ font-size: 22px; font-weight: 600; color: {text}; }}"
            f"QLabel#closePrompt {{ font-size: 13px; color: {muted}; }}"
            f"QPushButton#closeOption, QPushButton#closeOptionDanger {{"
            f"  background-color: {panel}; border: 1px solid {divider};"
            "  border-radius: 16px; text-align: left; }"
            "QPushButton#closeOption:hover {"
            "  background-color: rgba(0, 0, 0, 0.04);"
            f"  border-color: {accent_fg}; }}"
            f"QPushButton#closeOptionDanger:hover {{ background-color: {danger_bg};"
            f"  border-color: {_DANGER}; }}"
            f"QLabel#closeOptionIcon {{ background-color: {accent_bg};"
            "  border-radius: 12px; }"
            f"QLabel#closeOptionIconDanger {{ background-color: {danger_bg};"
            "  border-radius: 12px; }"
            f"QLabel#closeOptionTitle {{ font-size: 15px; font-weight: 600; color: {text}; }}"
            f"QLabel#closeOptionTitleDanger {{ font-size: 15px; font-weight: 600; color: {_DANGER}; }}"
            f"QLabel#closeOptionDescription {{ font-size: 12px; color: {muted}; }}"
            f"QCheckBox#closeRemember {{ font-size: 12px; color: {muted}; }}"
            "QCheckBox#closeRemember::indicator { width: 16px; height: 16px; }"
            f"QPushButton#closeCancelButton {{ min-width: 96px; min-height: 34px;"
            f"  border-radius: 17px; border: 1px solid {divider}; background-color: {panel};"
            f"  color: {muted}; font-size: 13px; }}"
            f"QPushButton#closeCancelButton:hover {{ background-color: {accent_bg};"
            f"  color: {text}; }}"
        )

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )


__all__ = ["CloseDialog"]
