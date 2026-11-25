"""Application-styled text input dialog.

Native ``QInputDialog`` keeps the OS title bar and button metrics, which is what
made inline renames look like a different application. This card matches the
rest of the dialogs: rounded surface, app title row, accent primary button.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon
from .dialog_theme import dialog_tokens

_DEFAULT_WIDTH = 420


class InputDialog(QDialog):
    """Modal single-line text prompt returning ``(text, accepted)``."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str,
        label: str,
        text: str = "",
        placeholder: str = "",
        ok_label: str | None = None,
        cancel_label: str | None = None,
    ) -> None:
        super().__init__(parent)
        ok_label = ok_label or tr("dialog.ok")
        cancel_label = cancel_label or tr("dialog.cancel")
        self._accepted = False
        self.setObjectName("inputDialog")
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(_DEFAULT_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._shell = QWidget(self)
        self._shell.setObjectName("inputShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

        layout = QVBoxLayout(self._shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self._shell)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 14, 0)
        header_layout.setSpacing(8)
        title_label = QLabel(title, header)
        title_label.setObjectName("inputTitle")
        title_label.setWordWrap(True)
        header_layout.addWidget(title_label, 1)
        self._close_button = QToolButton(header)
        self._close_button.setObjectName("inputCloseButton")
        self._close_button.setFixedSize(30, 30)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.clicked.connect(self.reject)
        header_layout.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        body = QWidget(self._shell)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 10, 24, 0)
        body_layout.setSpacing(8)
        label_widget = QLabel(label, body)
        label_widget.setObjectName("inputLabel")
        label_widget.setWordWrap(True)
        body_layout.addWidget(label_widget)

        self._line_edit = QLineEdit(body)
        self._line_edit.setObjectName("inputField")
        self._line_edit.setText(text)
        self._line_edit.setPlaceholderText(placeholder)
        self._line_edit.setMinimumHeight(34)
        self._line_edit.returnPressed.connect(self._accept)
        body_layout.addWidget(self._line_edit)
        layout.addWidget(body)

        actions = QWidget(self._shell)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(24, 18, 24, 20)
        actions_layout.setSpacing(8)
        actions_layout.addStretch(1)
        self._cancel_button = QPushButton(cancel_label, actions)
        self._cancel_button.setObjectName("inputSecondaryButton")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setMinimumHeight(32)
        self._cancel_button.clicked.connect(self.reject)
        actions_layout.addWidget(self._cancel_button)
        self._ok_button = QPushButton(ok_label, actions)
        self._ok_button.setObjectName("inputPrimaryButton")
        self._ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok_button.setMinimumHeight(32)
        self._ok_button.setDefault(True)
        self._ok_button.clicked.connect(self._accept)
        actions_layout.addWidget(self._ok_button)
        layout.addWidget(actions)

        self._apply_close_icon()
        self.setStyleSheet(self._stylesheet())
        self._line_edit.selectAll()

    # ------------------------------------------------------------------
    def value(self) -> str:
        return self._line_edit.text()

    def _accept(self) -> None:
        self._accepted = True
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
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
        self._line_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------
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

    def _stylesheet(self) -> str:
        t = dialog_tokens(self.palette())
        return (
            "QDialog#inputDialog { background: transparent; }"
            f"QWidget#inputShell {{ background-color: {t['shell']}; border-radius: 14px;"
            f"  border: 1px solid {t['divider']}; }}"
            f"QLabel#inputTitle {{ font-size: 16px; font-weight: 600; color: {t['text']}; }}"
            f"QLabel#inputLabel {{ font-size: 13px; color: {t['muted']}; }}"
            f"QLineEdit#inputField {{ min-height: 34px; padding: 0 10px; border-radius: 8px;"
            f"  border: 1px solid {t['divider']}; background-color: {t['field']};"
            f"  color: {t['text']}; font-size: 13px; selection-background-color: {t['accent']};"
            "  selection-color: #FFFFFF; }"
            f"QLineEdit#inputField:focus {{ border: 1px solid {t['accent']}; }}"
            f"QToolButton#inputCloseButton {{ border: none; border-radius: 8px;"
            "  background: transparent; }"
            f"QToolButton#inputCloseButton:hover {{ background-color: {t['hover']}; }}"
            f"QPushButton#inputSecondaryButton {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: 1px solid {t['divider']};"
            f"  background-color: {t['secondary']}; color: {t['text']}; font-size: 13px; }}"
            f"QPushButton#inputSecondaryButton:hover {{ background-color: {t['hover']}; }}"
            f"QPushButton#inputPrimaryButton {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: none; background-color: {t['accent']};"
            f"  color: {t['on_accent']}; font-size: 13px; font-weight: 600; }}"
        )


def prompt_text(
    parent: QWidget | None,
    *,
    title: str,
    label: str,
    text: str = "",
    placeholder: str = "",
) -> tuple[str, bool]:
    """Show the styled prompt and return ``(value, accepted)``."""

    dialog = InputDialog(
        parent, title=title, label=label, text=text, placeholder=placeholder
    )
    ok = dialog.exec() == QDialog.DialogCode.Accepted and dialog._accepted
    return dialog.value(), bool(ok)


__all__ = ["InputDialog", "prompt_text"]
