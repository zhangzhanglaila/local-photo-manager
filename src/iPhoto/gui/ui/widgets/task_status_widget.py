"""Status-bar entry that opens a background-task popup (IDE style).

The status bar only has room for one line, so it shows the most recent running
task with a percentage; clicking it expands every task with its own progress bar,
which is what makes a scan and an index rebuild visible at the same time.

Two details matter for the "IDE" feel: rows are updated in place (they used to be
re-created on every tick, which stacked a duplicate row per update), and the bars
ease towards their target so a scan that jumps 0 → 2 → 4 % does not look like a
glitch.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ...services.background_tasks import BackgroundTask, background_tasks
from .dialog_theme import dialog_tokens


class SmoothProgressBar(QProgressBar):
    """Progress bar that walks towards its target instead of jumping."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 100)
        self.setValue(0)
        self._target = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._step)

    def set_target(self, value: int) -> None:
        self._target = max(0, min(100, int(value)))
        if self.minimum() == self.maximum():  # was indeterminate
            self.setRange(0, 100)
            self.setValue(0)
        if self.value() != self._target and not self._timer.isActive():
            self._timer.start()

    def set_value_immediate(self, value: int) -> None:
        """Jump to *value* (used when a row is first created)."""

        self._timer.stop()
        if self.minimum() == self.maximum():
            self.setRange(0, 100)
        self._target = max(0, min(100, int(value)))
        self.setValue(self._target)

    def set_indeterminate(self) -> None:
        self._timer.stop()
        self.setRange(0, 0)

    def _step(self) -> None:
        current = self.value()
        difference = self._target - current
        if difference == 0:
            self._timer.stop()
            return
        # ~5% per tick: fast enough to keep up, slow enough to read as motion.
        step = max(1, abs(difference) // 5)
        self.setValue(current + (step if difference > 0 else -step))


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

        self._bar = SmoothProgressBar(self)
        self._bar.setObjectName("taskStatusBarMicro")
        self._bar.setFixedSize(90, 4)
        layout.addWidget(self._bar)

        self._popup = TaskPopup(self)
        self._popup.hide()
        QApplication.instance().installEventFilter(self)

        self._applying_style = False
        self._last_task_id: str | None = None
        self._apply_style()
        self._registry = background_tasks()
        self._registry.changed.connect(self._refresh)
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
        tokens = dialog_tokens(self)
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
        first = task.id != self._last_task_id
        self._last_task_id = task.id
        if percent is None:
            self._bar.set_indeterminate()
        elif first or self._bar.value() == 0:
            # Show the real value as soon as the task appears, then ease.
            self._bar.set_value_immediate(percent)
        else:
            self._bar.set_target(percent)
        if self._popup.isVisible():
            self._popup.refresh()

    # ------------------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if self._popup.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress:
                target = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else None
                if target is not None:
                    inside = self._popup.geometry().contains(self._popup.mapFromGlobal(target))
                    on_entry = self.rect().contains(self.mapFromGlobal(target))
                    if not inside and not on_entry:
                        self._popup.hide()
            elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._popup.hide()
        return super().eventFilter(watched, event)

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
        window = self.window()
        self._popup.setParent(window)
        anchor = self.mapTo(window, QPoint(0, 0))
        popup = self._popup
        width = popup.width()
        height = popup.sizeHint().height()
        # Right edge aligned with the entry, opened upwards, clamped inside the
        # window so it never hangs over the desktop or past the app border.
        x = anchor.x() + self.width() - width
        y = anchor.y() - height - 8
        margin = 12
        x = max(margin, min(x, window.width() - width - margin))
        y = max(margin, min(y, window.height() - height - margin))
        popup.move(x, y)
        popup.show()
        popup.raise_()


class TaskRow(QWidget):
    """One task line: title, state, bar and detail."""

    def __init__(self, task: BackgroundTask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(task.title, self)
        self._title.setObjectName("taskPopupTitle")
        header.addWidget(self._title, 1)
        self._state = QLabel("", self)
        self._state.setObjectName("taskPopupState")
        header.addWidget(self._state, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)

        self._bar = SmoothProgressBar(self)
        self._bar.setObjectName("taskPopupBar")
        self._bar.setFixedHeight(5)
        layout.addWidget(self._bar)

        self._detail = QLabel("", self)
        self._detail.setObjectName("taskPopupDetail")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

        self.update_task(task, animate=False)

    def update_task(self, task: BackgroundTask, *, animate: bool = True) -> None:
        self._title.setText(task.title)
        if task.state == "failed":
            self._state.setText(tr("status.tasks_failed"))
        elif task.state == "done":
            self._state.setText(tr("status.tasks_done"))
        elif task.percent is None:
            self._state.setText("…")
        else:
            self._state.setText(f"{task.percent}%")

        if task.percent is None:
            self._bar.set_indeterminate()
        elif animate:
            self._bar.set_target(task.percent)
        else:
            self._bar.set_value_immediate(task.percent)

        detail = task.detail
        if task.total > 0:
            detail = f"{task.done}/{task.total}" + (f"  ·  {task.detail}" if task.detail else "")
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))


class TaskPopup(QFrame):
    """Card listing running and recently finished tasks."""

    def __init__(self, anchor: QWidget) -> None:
        # A child of the main window, not a top-level Popup: a popup window has
        # its own square background and can spill outside the rounded app shell,
        # which looked broken. As a child it is clipped by the window and the
        # corners show the window background through the border radius.
        super().__init__(anchor.window() if anchor is not None else None)
        self.setObjectName("taskPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._shell = QFrame(self)
        self._shell.setObjectName("taskPopupShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

        self._layout = QVBoxLayout(self._shell)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(10)

        self._empty = QLabel(tr("status.tasks_empty"), self._shell)
        self._empty.setObjectName("taskPopupEmpty")
        self._layout.addWidget(self._empty)

        #: Rows are updated in place; re-creating them on every tick multiplied
        #: the list by the number of progress updates.
        self._rows: dict[str, TaskRow] = {}
        self._applying_style = False
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self.refresh)
        self._apply_style()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        tasks = background_tasks().tasks()
        self._empty.setVisible(not tasks)
        wanted = {task.id for task in tasks}

        for task_id, row in list(self._rows.items()):
            if task_id not in wanted:
                self._layout.removeWidget(row)
                row.setParent(None)
                row.deleteLater()
                self._rows.pop(task_id, None)

        for task in tasks:
            row = self._rows.get(task.id)
            if row is None:
                row = TaskRow(task, self)
                self._rows[task.id] = row
                self._layout.addWidget(row)
            else:
                row.update_task(task)

        if tasks:
            self._timer.start()
        else:
            self._timer.stop()
        self.adjustSize()

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        # Re-read the palette on every open: the window theme may have changed
        # while the popup was hidden.
        self._apply_style()
        super().showEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.PaletteChange and not self._applying_style:
            self._applying_style = True
            try:
                self._apply_style()
            finally:
                self._applying_style = False
        super().changeEvent(event)

    def _apply_style(self) -> None:
        tokens = dialog_tokens(self)
        self.setStyleSheet(
            "QFrame#taskPopup { background: transparent; border: none; }"
            f"QFrame#taskPopupShell {{ background-color: {tokens['shell']};"
            f"  border: 1px solid {tokens['divider']}; border-radius: 12px; }}"
            f"QLabel#taskPopupTitle {{ font-size: 12px; font-weight: 600; color: {tokens['text']}; }}"
            f"QLabel#taskPopupState {{ font-size: 11px; color: {tokens['muted']}; }}"
            f"QLabel#taskPopupDetail {{ font-size: 11px; color: {tokens['muted']}; }}"
            f"QLabel#taskPopupEmpty {{ font-size: 12px; color: {tokens['muted']}; }}"
            f"QProgressBar#taskPopupBar {{ border: none; background-color: {tokens['secondary']};"
            "  border-radius: 2px; }"
            f"QProgressBar#taskPopupBar::chunk {{ background-color: {tokens['accent']};"
            "  border-radius: 2px; }"
        )


__all__ = ["SmoothProgressBar", "TaskPopup", "TaskRow", "TaskStatusButton"]
