"""Duplicate detection and smart-album commands, separate from window assembly.

The two menu commands used to live in ``MainCoordinator``: they only need the
active library session (for the worker), the gallery view model (to show the
result) and the status bar (to explain an empty result). That is small enough to
own its own object; the coordinator keeps the menu wiring and nothing else.
"""

from __future__ import annotations

from PySide6.QtCore import QObject

from iPhoto.gui.services import organize_workers
from iPhoto.i18n import tr


class OrganizeCoordinator(QObject):
    """Run duplicate detection / smart-album workers and present their results."""

    def __init__(self, context, gallery_vm, status_bar, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._gallery_vm = gallery_vm
        self._status_bar = status_bar

    def find_duplicates(self) -> None:
        """Start duplicate detection for the active library session."""

        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        # Duplicate detection and smart albums read album metadata only; they no
        # longer need the CLIP embedding service, and gating them on it made both
        # features unavailable (and could trigger a model-download dialog).
        organize_workers.find_duplicates(library_session, self._display_duplicates)

        self._status_bar.show_message(tr("organize.searching_duplicates"))

    def _display_duplicates(self, duplicates: list) -> None:
        """Show duplicate photos in the gallery."""

        if not duplicates:
            self._status_bar.show_message(tr("organize.no_duplicates"))
            return

        all_asset_ids = []
        for group in duplicates:
            all_asset_ids.extend(group.asset_ids)

        self._gallery_vm.show_search_results(all_asset_ids)

        self._status_bar.show_message(
            tr(
                "organize.duplicates_found",
                groups=len(duplicates),
                photos=len(all_asset_ids),
            )
        )

    def create_smart_albums(self, group_by: str) -> None:
        """Start smart-album grouping for *group_by*.

        Parameters
        ----------
        group_by : str
            How to group photos: 'event', 'location', 'time', 'theme'.
        """

        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        organize_workers.create_smart_albums(
            library_session,
            group_by,
            lambda albums: self._display_smart_albums(albums, group_by),
        )

        self._status_bar.show_message(
            tr(
                "organize.creating_albums",
                group_by=tr(f"organize.group.{group_by}"),
            )
        )

    def _display_smart_albums(self, albums: list, group_by: str) -> None:
        """Show the first suggested album's photos in the gallery."""

        if not albums:
            self._status_bar.show_message(tr("organize.no_albums"))
            return

        # For now, show the first album's photos in the gallery; in a full
        # implementation these would be created as real albums in the library.
        first_album = albums[0]
        self._gallery_vm.show_search_results(first_album.asset_ids)

        self._status_bar.show_message(
            tr("organize.albums_created", default=f"Created {len(albums)} smart albums")
        )


__all__ = ["OrganizeCoordinator"]
