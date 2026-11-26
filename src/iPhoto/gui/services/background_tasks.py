"""Background tasks shown by the status-bar progress button.

The status bar used to be a single line of text plus a progress bar that only the
most recent operation could own: a scan that started while an index rebuild was
running simply overwrote the other one. This registry keeps every running task so
the UI can list them (like the background-task popup in an IDE) and so finishing
one task no longer erases another.

The registry is created by ``bootstrap`` and injected into the coordinator, the
status-bar controller and the task popup; there is no process-wide accessor, so a
second window or a headless test cannot share the first window's tasks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal


@dataclass
class BackgroundTask:
    """One long-running operation."""

    id: str
    title: str
    detail: str = ""
    done: int = 0
    total: int = 0  # 0 means "unknown / indeterminate"
    state: str = "running"  # running | done | failed
    updated_at: float = field(default_factory=time.time)

    @property
    def percent(self) -> int | None:
        if self.total <= 0:
            return None
        return max(0, min(100, int(self.done * 100 / self.total)))

    @property
    def running(self) -> bool:
        return self.state == "running"


class BackgroundTaskRegistry(QObject):
    """Observable list of background tasks."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._tasks: dict[str, BackgroundTask] = {}
        #: How long finished tasks stay in the popup list.
        self.finished_ttl_s = 20.0

    # ------------------------------------------------------------------
    def start(self, task_id: str, title: str, detail: str = "") -> BackgroundTask:
        task = BackgroundTask(id=task_id, title=title, detail=detail)
        self._tasks[task_id] = task
        self.changed.emit()
        return task

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        detail: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        if title is not None:
            task.title = title
        if detail is not None:
            task.detail = detail
        if done is not None:
            task.done = max(0, int(done))
        if total is not None:
            task.total = max(0, int(total))
        task.updated_at = time.time()
        self.changed.emit()

    def finish(self, task_id: str, *, failed: bool = False, detail: str = "") -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.state = "failed" if failed else "done"
        if detail:
            task.detail = detail
        task.updated_at = time.time()
        self.changed.emit()

    def task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    def tasks(self) -> list[BackgroundTask]:
        """Running tasks first, then recently finished ones."""

        self._prune()
        running = [t for t in self._tasks.values() if t.running]
        done = [t for t in self._tasks.values() if not t.running]
        done.sort(key=lambda t: t.updated_at, reverse=True)
        return running + done

    def running_tasks(self) -> list[BackgroundTask]:
        return [task for task in self.tasks() if task.running]

    def latest(self) -> BackgroundTask | None:
        tasks = self.running_tasks()
        return tasks[0] if tasks else None

    def clear(self) -> None:
        self._tasks.clear()
        self.changed.emit()

    def _prune(self) -> None:
        now = time.time()
        expired = [
            task_id
            for task_id, task in self._tasks.items()
            if not task.running and now - task.updated_at > self.finished_ttl_s
        ]
        for task_id in expired:
            self._tasks.pop(task_id, None)


__all__ = [
    "BackgroundTask",
    "BackgroundTaskRegistry",
]
