"""Status-bar entry that opens a background-task popup (IDE style).

The status bar only has room for one line, so it shows the most recent running
task with a percentage; clicking it expands every task with its own progress bar,
which is what makes a scan and an index rebuild visible at the same time.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....agent.services.search_status import search_status
from ....i18n import tr
from ...services.background_tasks import BackgroundTask, background_tasks
from .dialog_theme import dialog_tokens


class TaskStatusButton(QWidget):
    """Compact "background tasks" entry with a popup list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskStatusButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)

        self._label = QLabel(self)
        self._label.setObjectName("taskStatusLabel")
        layout.addWidget(self._label)

        self._bar = QProgressBar(self)
        self._bar.setObjectName("taskStatusBarMicro")
        self._bar.setTextVisible(False)
        self._bar.setFixedSize(90, 4)
        self._bar.setRange(0, 100)
        layout.addWidget(self._bar)

        self._popup = TaskPopup(self)
        self._popup.hide()

        self._applying_style = False
        self._apply_style()
        self._registry = background_tasks()
        self._registry.changed.connect(self._refresh)
        search_status().changed.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------
    def changeEvent(self, event) -> None:  # noqa: N802
        # ``setStyleSheet`` re-emits PaletteChange, so re-applying without a guard
        # recursed until Qt aborted the call.
        if event.type() == QEvent.Type.PaletteChange and not self._applying_style:
            self._applying_style = True
            try:
                self._apply_style()
            finally:
                self._applying_style = False
        super().changeEvent(event)

    def _apply_style(self) -> None:
        tokens = dialog_tokens(self.palette())
        self.setStyleSheet(
            "QWidget#taskStatusButton { border-radius: 6px; background: transparent; }"
            f"QWidget#taskStatusButton:hover {{ background-color: {tokens['hover']}; }}"
            f"QLabel#taskStatusLabel {{ font-size: 11px; color: {tokens['muted']}; }}"
            f"QProgressBar#taskStatusBarMicro {{ border: none;"
            f"  background-color: {tokens['secondary']}; border-radius: 2px; }}"
            f"QProgressBar#taskStatusBarMicro::chunk {{ background-color: {tokens['accent']};"
            "  border-radius: 2px; }"
        )

    def _refresh(self) -> None:
        task = self._registry.latest()
        if task is None:
            self.hide()
            if self._popup.isVisible():
                self._popup.hide()
            return
        self.show()
        percent = task.percent
        text = task.title
        if percent is not None:
            text = f"{task.title} {percent}%"
        self._label.setText(text)
        if percent is None:
            self._bar.setRange(0, 0)
        else:
            self._bar.setRange(0, 100)
            self._bar.setValue(percent)
        if self._popup.isVisible():
            self._popup.refresh()

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def _toggle_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()
            return
        self._popup.refresh()
        anchor = self.mapToGlobal(QPoint(0, 0))
        self._popup.move(anchor.x(), anchor.y() - self._popup.sizeHint().height() - 8)
        self._popup.show()
        self._popup.raise_()


class TaskPopup(QFrame):
    """Card listing running and recently finished tasks."""

    def __init__(self, anchor: QWidget) -> None:
        super().__init__(anchor, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("taskPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(360)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(10)

        self._empty = QLabel(tr("status.tasks_empty"), self)
        self._empty.setObjectName("taskPopupEmpty")
        self._layout.addWidget(self._empty)

        self._rows: list[QWidget] = []
        self._applying_style = False
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self.refresh)
        self._apply_style()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        while self._rows:
            row = self._rows.pop()
            row.setParent(None)
            row.deleteLater()

        tasks = background_tasks().tasks()
        self._empty.setVisible(not tasks)
        for task in tasks:
            self._layout.addWidget(self._row(task))
        if tasks:
            self._timer.start()
        else:
            self._timer.stop()
        self.adjustSize()

    def _row(self, task: BackgroundTask) -> QWidget:
        row = QWidget(self)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel(task.title, row)
        title.setObjectName("taskPopupTitle")
        header.addWidget(title, 1)
        percent = task.percent
        suffix = ""
        if percent is not None:
            suffix = f"{percent}%"
        elif task.running:
            suffix = "…"
        if task.state == "failed":
            suffix = tr("status.tasks_failed")
        elif task.state == "done":
            suffix = tr("status.tasks_done")
        state = QLabel(suffix, row)
        state.setObjectName("taskPopupState")
        header.addWidget(state, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)

        bar = QProgressBar(row)
        bar.setObjectName("taskPopupBar")
        bar.setFixedHeight(5)
        bar.setTextVisible(False)
        if percent is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 100)
            bar.setValue(percent)
        layout.addWidget(bar)

        detail_text = task.detail
        if task.total > 0:
            detail_text = (
                f"{task.done}/{task.total}"
                + (f"  ·  {task.detail}" if task.detail else "")
            )
        if detail_text:
            detail = QLabel(detail_text, row)
            detail.setObjectName("taskPopupDetail")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        return row

    # ------------------------------------------------------------------
    def changeEvent(self, event) -> None:  # noqa: N802
        # ``setStyleSheet`` re-emits PaletteChange, so re-applying without a guard
        # recursed until Qt aborted the call.
        if event.type() == QEvent.Type.PaletteChange and not self._applying_style:
            self._applying_style = True
            try:
                self._apply_style()
            finally:
                self._applying_style = False
        super().changeEvent(event)

    def _apply_style(self) -> None:
        tokens = dialog_tokens(self.palette())
        self.setStyleSheet(
            f"QFrame#taskPopup {{ background-color: {tokens['shell']};"
            f"  border: 1px solid {tokens['divider']}; border-radius: 12px; }}"
            f"QFrame#taskPopup > QWidget {{ background: transparent; }}"
            f"QLabel#taskPopupTitle {{ font-size: 12px; font-weight: 600; color: {tokens['text']}; }}"
            f"QLabel#taskPopupState {{ font-size: 11px; color: {tokens['muted']}; }}"
            f"QLabel#taskPopupDetail {{ font-size: 11px; color: {tokens['muted']}; }}"
            f"QLabel#taskPopupEmpty {{ font-size: 12px; color: {tokens['muted']}; }}"
            f"QProgressBar#taskPopupBar {{ border: none; background-color: {tokens['secondary']};"
            "  border-radius: 2px; }"
            f"QProgressBar#taskPopupBar::chunk {{ background-color: {tokens['accent']};"
            "  border-radius: 2px; }"
        )


__all__ = ["TaskPopup", "TaskStatusButton"]
