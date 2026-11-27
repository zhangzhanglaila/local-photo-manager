"""Background jobs behind the "find duplicates" and "smart albums" actions.

Both used to be classes defined inside ``MainCoordinator`` methods, which meant
the coordinator also owned ``OrganizeService`` construction and error
reporting.  Each action is now one call here: build the service from the
library session, run the job on the global thread pool, and hand the result to
a callback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from iPhoto.agent.services.organize_service import OrganizeService

_LOGGER = logging.getLogger(__name__)

#: Similarity above which two photos count as duplicates.
DUPLICATE_SIMILARITY_THRESHOLD = 0.95


class OrganizeSession(Protocol):
    """The parts of a library session an organize job reads."""

    @property
    def library_root(self) -> Any: ...

    @property
    def asset_runtime(self) -> Any: ...


#: Bridges waiting for a queued delivery.  A ``QObject`` must outlive its own
#: queued signal, so the module keeps them alive until the result arrives.
_PENDING: set["_ResultBridge"] = set()


class _ResultBridge(QObject):
    """Hand a job result back on the thread that created the bridge.

    Emitting from the pool thread with a receiver living in the GUI thread makes
    Qt queue the call, which is the only safe way to touch widgets from a
    worker.  ``QTimer.singleShot`` cannot be used for this: a pool thread has no
    event loop, so a timer created there never fires and the result is dropped.
    """

    ready = Signal(object)

    def __init__(self, callback: Callable[[list], None]) -> None:
        super().__init__()
        self._callback = callback
        self.ready.connect(self._deliver)

    def _deliver(self, result: list) -> None:
        _PENDING.discard(self)
        self._callback(result)


def build_organize_service(session: OrganizeSession) -> OrganizeService:
    """Return an ``OrganizeService`` bound to *session*'s assets and root."""

    return OrganizeService(
        asset_repository=session.asset_runtime.assets,
        library_root=session.library_root,
    )


class _OrganizeJob(QRunnable):
    """Run one ``OrganizeService`` call off the GUI thread.

    A failing service call reports an empty result, matching the previous
    behaviour: the user sees "nothing found" instead of an exception dialog.
    """

    def __init__(
        self,
        *,
        label: str,
        task: Callable[[OrganizeService], list],
        service: OrganizeService,
        bridge: _ResultBridge,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._label = label
        self._task = task
        self._service = service
        self._bridge = bridge

    @Slot()
    def run(self) -> None:
        try:
            result = self._task(self._service)
        except Exception:
            _LOGGER.exception("%s failed", self._label)
            result = []
        self._bridge.ready.emit(result)


def _dispatch(
    *,
    label: str,
    task: Callable[[OrganizeService], list],
    session: OrganizeSession,
    callback: Callable[[list], None],
    pool: QThreadPool | None,
) -> None:
    bridge = _ResultBridge(callback)
    _PENDING.add(bridge)
    job = _OrganizeJob(
        label=label,
        task=task,
        service=build_organize_service(session),
        bridge=bridge,
    )
    (pool or QThreadPool.globalInstance()).start(job)


def find_duplicates(
    session: OrganizeSession,
    callback: Callable[[list], None],
    *,
    pool: QThreadPool | None = None,
) -> None:
    """Detect near-duplicate photos in *session* and pass the groups to *callback*."""

    _dispatch(
        label="Duplicate detection",
        task=lambda service: service.find_duplicates(
            threshold=DUPLICATE_SIMILARITY_THRESHOLD
        ),
        session=session,
        callback=callback,
        pool=pool,
    )


def create_smart_albums(
    session: OrganizeSession,
    group_by: str,
    callback: Callable[[list], None],
    *,
    pool: QThreadPool | None = None,
) -> None:
    """Group *session*'s photos into smart albums and pass them to *callback*."""

    _dispatch(
        label="Smart album creation",
        task=lambda service: service.create_smart_albums(group_by=group_by),
        session=session,
        callback=callback,
        pool=pool,
    )


__all__ = [
    "DUPLICATE_SIMILARITY_THRESHOLD",
    "build_organize_service",
    "create_smart_albums",
    "find_duplicates",
]
