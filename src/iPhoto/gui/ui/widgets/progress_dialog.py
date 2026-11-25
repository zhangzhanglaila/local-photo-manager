"""Application-styled progress dialog.

``QProgressDialog`` brings the OS title bar and button metrics, and it shows up
during long copies and model downloads, so it read as a foreign window next to
the rest of the app. This card shows the same information in the app's chrome.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .dialog_theme import dialog_tokens


class ProgressDialog(QDialog):
    """Frameless progress card with an optional dismiss button."""

    dismissed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str,
        message: str = "",
        dismiss_label: str | None = None,
        width: int = 420,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("progressDialog")
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(width)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._shell = QWidget(self)
        self._shell.setObjectName("progressShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

        layout = QVBoxLayout(self._shell)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        self._title_label = QLabel(title, self._shell)
        self._title_label.setObjectName("progressTitle")
        layout.addWidget(self._title_label)

        self._message_label = QLabel(message, self._shell)
        self._message_label.setObjectName("progressMessage")
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        self._bar = QProgressBar(self._shell)
        self._bar.setObjectName("progressBar")
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        self._actions = QWidget(self._shell)
        actions_layout = QHBoxLayout(self._actions)
        actions_layout.setContentsMargins(0, 4, 0, 0)
        actions_layout.addStretch(1)
        self._dismiss_button = QPushButton(dismiss_label or "", self._actions)
        self._dismiss_button.setObjectName("progressDismissButton")
        self._dismiss_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss_button.setMinimumHeight(30)
        self._dismiss_button.clicked.connect(self._on_dismiss)
        actions_layout.addWidget(self._dismiss_button)
        self._actions.setVisible(bool(dismiss_label))
        layout.addWidget(self._actions)

        self.setStyleSheet(self._stylesheet())

    # ------------------------------------------------------------------
    def set_title(self, title: str) -> None:
        self.setWindowTitle(title)
        self._title_label.setText(title)

    def set_message(self, message: str) -> None:
        self._message_label.setText(message)

    def set_progress(self, value: int, maximum: int = 100) -> None:
        """Set the bar; ``maximum <= 0`` switches to the indeterminate mode."""

        if maximum <= 0:
            self._bar.setRange(0, 0)
            return
        if self._bar.maximum() == 0:
            self._bar.setRange(0, maximum)
        self._bar.setMaximum(max(1, maximum))
        self._bar.setValue(max(0, min(value, self._bar.maximum())))

    def allow_close(self) -> None:
        """Compatibility shim: the card can always be closed programmatically."""

    # ------------------------------------------------------------------
    def _on_dismiss(self) -> None:
        self.dismissed.emit()
        self.close()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.window().frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )

    # ------------------------------------------------------------------
    def _stylesheet(self) -> str:
        t = dialog_tokens(self.palette())
        return (
            "QDialog#progressDialog { background: transparent; }"
            f"QWidget#progressShell {{ background-color: {t['shell']}; border-radius: 14px;"
            f"  border: 1px solid {t['divider']}; }}"
            f"QLabel#progressTitle {{ font-size: 15px; font-weight: 600; color: {t['text']}; }}"
            f"QLabel#progressMessage {{ font-size: 12px; color: {t['muted']}; }}"
            "QProgressBar#progressBar { border: none; background-color: "
            f"  {t['secondary']}; border-radius: 3px; }}"
            f"QProgressBar#progressBar::chunk {{ background-color: {t['accent']};"
            "  border-radius: 3px; }"
            f"QPushButton#progressDismissButton {{ min-height: 30px; padding: 0 16px;"
            f"  border-radius: 8px; border: 1px solid {t['divider']};"
            f"  background-color: {t['secondary']}; color: {t['text']}; font-size: 12px; }}"
            f"QPushButton#progressDismissButton:hover {{ background-color: {t['hover']}; }}"
        )


__all__ = ["ProgressDialog"]
