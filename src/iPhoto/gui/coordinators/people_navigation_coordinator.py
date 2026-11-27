"""Open a People cluster/group gallery from the dashboard.

The dashboard emits a person/group id; opening it means building the cluster
query, hiding the dashboard and showing the gallery. That is one small workflow,
so it no longer lives in ``MainCoordinator``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer


class PeopleNavigationCoordinator(QObject):
    """Turn People dashboard activations into gallery cluster views."""

    def __init__(self, window, gallery_vm, view_router, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._gallery_vm = gallery_vm
        self._view_router = view_router
        self._logger = logging.getLogger(__name__)

    def on_cluster_activated(self, person_id: str) -> None:
        QTimer.singleShot(0, lambda: self._open(person_id, kind="person"))

    def on_group_activated(self, group_id: str) -> None:
        QTimer.singleShot(0, lambda: self._open(group_id, kind="group"))

    def _open(self, entity_id: str, *, kind: str) -> None:
        page = self._window.ui.people_page
        try:
            if kind == "person":
                query = page.build_cluster_query(entity_id)
            else:
                query = page.build_group_query(entity_id)
            if not query.asset_ids:
                return
            page.prepare_for_hide()
            self._gallery_vm.open_people_cluster_gallery(
                query,
                kind=kind,
                entity_id=entity_id,
            )
            self._view_router.show_gallery()
        except Exception:
            self._logger.exception(
                "Failed to open people %s gallery for %s", kind, entity_id
            )


__all__ = ["PeopleNavigationCoordinator"]
