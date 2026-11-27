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

import logging
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

_LOGGER = logging.getLogger(__name__)


@dataclass
class BackgroundTask:
    """One long-running operation."""

    id: str
    title: str
    detail: str = ""
    done: int = 0
    total: int = 0  # 0 means "unknown / indeterminate"
    #: ``running`` is doing work now, ``waiting`` is queued behind another AI
    #: scan, ``done``/``failed`` are finished and only linger in the popup.
    state: str = "running"
    updated_at: float = field(default_factory=time.time)

    @property
    def percent(self) -> int | None:
        if self.total <= 0:
            return None
        return max(0, min(100, int(self.done * 100 / self.total)))

    @property
    def running(self) -> bool:
        """A waiting task is still unfinished, so both states count as running."""

        return self.state in ("running", "waiting")

    @property
    def waiting(self) -> bool:
        return self.state == "waiting"


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
        # A task with an empty label looked like a bare progress bar in the
        # popup, so fall back to the id rather than rendering an invisible row.
        task = BackgroundTask(id=task_id, title=title or task_id, detail=detail)
        self._tasks[task_id] = task
        _LOGGER.debug("task start id=%s title=%r", task_id, task.title)
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

    def set_state(
        self,
        task_id: str,
        state: str,
        *,
        detail: str | None = None,
    ) -> None:
        """Move a task between ``running`` and ``waiting`` (or finish it).

        AI scans are serialized, so one worker can sit behind another for a
        while. ``waiting`` keeps it in the popup without letting it win the
        compact status-bar label over the task that is actually moving.
        """

        task = self._tasks.get(task_id)
        if task is None:
            return
        changed = task.state != state
        task.state = state
        if detail is not None and task.detail != detail:
            task.detail = detail
            changed = True
        if changed:
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
        _LOGGER.debug(
            "task finish id=%s state=%s done=%s/%s detail=%r",
            task_id,
            task.state,
            task.done,
            task.total,
            task.detail,
        )
        self.changed.emit()

    def discard(self, task_id: str) -> None:
        """Remove a task without showing it as done (cancelled worker)."""

        if self._tasks.pop(task_id, None) is not None:
            _LOGGER.debug("task discard id=%s", task_id)
            self.changed.emit()

    def task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    def tasks(self) -> list[BackgroundTask]:
        """Finished tasks first, then the work that is still moving.

        The popup sits above the status bar and grows upward. Putting the
        finished history on top and the unfinished work underneath means a task
        that completes moves out of the way instead of leaving a hole at the
        bottom of the card.
        """

        self._prune()
        active = [t for t in self._tasks.values() if t.state == "running"]
        waiting = [t for t in self._tasks.values() if t.waiting]
        done = [t for t in self._tasks.values() if not t.running]
        # Newest finished first; among unfinished work, the most recently
        # active task comes first so it stays visible near the status bar.
        done.sort(key=lambda t: t.updated_at, reverse=True)
        active.sort(key=lambda t: t.updated_at, reverse=True)
        waiting.sort(key=lambda t: t.updated_at)
        return done + active + waiting

    def running_tasks(self) -> list[BackgroundTask]:
        return [task for task in self.tasks() if task.running]

    def latest(self) -> BackgroundTask | None:
        """The task the compact status-bar entry should name."""

        self._prune()
        active = [t for t in self._tasks.values() if t.state == "running"]
        if active:
            return max(active, key=lambda t: t.updated_at)
        waiting = [t for t in self._tasks.values() if t.waiting]
        if waiting:
            return max(waiting, key=lambda t: t.updated_at)
        return None

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
