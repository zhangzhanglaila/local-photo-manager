"""Library lifecycle events: album rename, People snapshot, move toast.

These handlers react to library-wide events rather than to a specific view.
They only need the pinned-items service, the thumbnail cache, the gallery view
model, and the toast widget, so they live together outside ``MainCoordinator``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.utils.pathutils import paths_equal


class LibraryEventsCoordinator(QObject):
    """Handle album renames, People snapshot refreshes and move completion."""

    def __init__(
        self,
        window,
        context,
        *,
        pinned_items_service,
        thumbnail_service,
        gallery_vm,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._pinned_items_service = pinned_items_service
        self._thumbnail_service = thumbnail_service
        self._gallery_vm = gallery_vm

    def on_album_renamed(self, old_path: Path, new_path: Path) -> None:
        self._pinned_items_service.remap_album_path(
            old_path,
            new_path,
            library_root=self._context.library.root(),
            fallback_label=new_path.name,
        )
        self._thumbnail_service.remap_album_paths(old_path, new_path)
        self._gallery_vm.handle_album_renamed(old_path, new_path)

    def on_people_snapshot(self, event: object) -> None:
        library_root = self._context.library.root()
        if (
            library_root is not None
            and getattr(event, "library_root", None) == library_root
        ):
            self._pinned_items_service.prune_missing_people_entities(
                library_root,
                person_ids=tuple(getattr(event, "changed_person_ids", ()) or ()),
                group_ids=tuple(getattr(event, "changed_group_ids", ()) or ()),
                person_redirects=dict(getattr(event, "person_redirects", {}) or {}),
                group_redirects=dict(getattr(event, "group_redirects", {}) or {}),
            )
        self._window.ui.sidebar.refresh_tree_model()

    def on_move_finished(
        self,
        source: Path,
        destination: Path,
        success: bool,
        message: str,
    ) -> None:
        """Show the lightweight completion toast for successful ordinary moves."""

        del message
        if not success or self._is_recently_deleted_move(source, destination):
            return
        self._window.ui.notification_toast.show_toast("Moved")

    def _is_recently_deleted_move(self, source: Path, destination: Path) -> bool:
        trash_root = self._context.library.deleted_directory()
        if trash_root is not None:
            return paths_equal(source, trash_root) or paths_equal(
                destination,
                trash_root,
            )
        return (
            source.name == RECENTLY_DELETED_DIR_NAME
            or destination.name == RECENTLY_DELETED_DIR_NAME
        )


__all__ = ["LibraryEventsCoordinator"]
