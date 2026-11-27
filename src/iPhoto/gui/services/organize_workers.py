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

from PySide6.QtCore import QRunnable, QThreadPool, Slot

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
        callback: Callable[[list], None],
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._label = label
        self._task = task
        self._service = service
        self._callback = callback

    @Slot()
    def run(self) -> None:
        try:
            result = self._task(self._service)
        except Exception:
            _LOGGER.exception("%s failed", self._label)
            result = []
        self._callback(result)


def find_duplicates(
    session: OrganizeSession,
    callback: Callable[[list], None],
    *,
    pool: QThreadPool | None = None,
) -> None:
    """Detect near-duplicate photos in *session* and pass the groups to *callback*."""

    job = _OrganizeJob(
        label="Duplicate detection",
        task=lambda service: service.find_duplicates(
            threshold=DUPLICATE_SIMILARITY_THRESHOLD
        ),
        service=build_organize_service(session),
        callback=callback,
    )
    (pool or QThreadPool.globalInstance()).start(job)


def create_smart_albums(
    session: OrganizeSession,
    group_by: str,
    callback: Callable[[list], None],
    *,
    pool: QThreadPool | None = None,
) -> None:
    """Group *session*'s photos into smart albums and pass them to *callback*."""

    job = _OrganizeJob(
        label="Smart album creation",
        task=lambda service: service.create_smart_albums(group_by=group_by),
        service=build_organize_service(session),
        callback=callback,
    )
    (pool or QThreadPool.globalInstance()).start(job)


__all__ = [
    "DUPLICATE_SIMILARITY_THRESHOLD",
    "build_organize_service",
    "create_smart_albums",
    "find_duplicates",
]
