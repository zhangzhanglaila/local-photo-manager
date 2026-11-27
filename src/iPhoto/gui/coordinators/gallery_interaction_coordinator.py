"""Grid clicks, selection sync and foreground-scan yielding for the main window.

These handlers all translate user interaction into calls on the gallery view
model, playback or library runtime. They are independent of menu assembly and
library opening, so they live here instead of inside ``MainCoordinator``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QTimer

from iPhoto.i18n import tr


class GalleryInteractionCoordinator(QObject):
    """Handle grid/media interaction that is not playback or editing itself."""

    def __init__(
        self,
        window,
        context,
        *,
        gallery_vm,
        gallery_store,
        asset_list_vm,
        dialog,
        facade,
        selection_controller,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._gallery_vm = gallery_vm
        self._gallery_store = gallery_store
        self._asset_list_vm = asset_list_vm
        self._dialog = dialog
        self._facade = facade
        self._selection_controller = selection_controller
        self._logger = logging.getLogger(__name__)

        self._media_failure_cleanup_paths: set[str] = set()
        self._scroll_viewing_registered = False
        self._scroll_idle_timer = QTimer(self)
        self._scroll_idle_timer.setSingleShot(True)
        self._scroll_idle_timer.setInterval(400)
        self._scroll_idle_timer.timeout.connect(self.on_scroll_idle)

    def on_media_load_failed(self, path: Path, message: str) -> None:
        path_key = str(path)
        if path_key in self._media_failure_cleanup_paths:
            return
        # Broad guard: block ALL media failure handling while any dialog is open
        # to prevent nested blocking dialogs from chained broken videos.
        if self._media_failure_cleanup_paths:
            return

        self._media_failure_cleanup_paths.add(path_key)
        try:
            self._dialog.show_error(
                tr("msg.file_unreadable", name=path.name, message=message)
            )
            updates = getattr(self._facade, "library_updates", None)
            if updates is None:
                return

            refresh_root = updates.handle_media_load_failure(path)
            if refresh_root is not None:
                # Defer the reload so it runs after the dialog's event loop exits,
                # preventing nested dialogs from chained broken video loads.
                QTimer.singleShot(0, self._gallery_store.reload_current_selection)
        finally:
            self._media_failure_cleanup_paths.discard(path_key)

    def on_video_playback_state_changed(self, is_playing: bool) -> None:
        """Let background scans yield while a video is playing."""

        library_manager = self._foreground_library()
        if library_manager is None:
            return
        if is_playing:
            library_manager.register_foreground_viewing()
        else:
            library_manager.unregister_foreground_viewing()

    def _foreground_library(self):
        return getattr(self._context, "library", None)

    def enter_foreground_viewing(self) -> None:
        """The user is looking at content: pause face/OCR scans."""

        library_manager = self._foreground_library()
        if library_manager is not None:
            library_manager.register_foreground_viewing()

    def leave_foreground_viewing(self) -> None:
        """Leaving the viewer: resume background scans."""

        library_manager = self._foreground_library()
        if library_manager is not None:
            library_manager.unregister_foreground_viewing()

    def touch_foreground_viewing(self) -> None:
        """Register brief foreground viewing: scans yield for a moment."""

        if not self._scroll_viewing_registered:
            self._scroll_viewing_registered = True
            library_manager = self._foreground_library()
            if library_manager is not None:
                library_manager.register_foreground_viewing()
        self._scroll_idle_timer.start()

    def on_gallery_scrolled(self, first: int, last: int) -> None:
        """Scroll handling: briefly yield during scrolling, resume when idle."""

        del first, last
        self.touch_foreground_viewing()

    def on_scroll_idle(self) -> None:
        """Scrolling stopped: release the foreground registration."""

        if not self._scroll_viewing_registered:
            return
        self._scroll_viewing_registered = False
        library_manager = self._foreground_library()
        if library_manager is not None:
            library_manager.unregister_foreground_viewing()

    def on_asset_clicked(self, index: QModelIndex) -> None:
        if self._selection_controller and self._selection_controller.is_active():
            return
        if not index.isValid():
            return
        row = index.row()
        if row < 0:
            return
        try:
            self._gallery_vm.open_row(row)
        except Exception:
            self._logger.exception("open_row failed for row %s", row)

    def on_favorite_clicked(self, index: QModelIndex) -> None:
        self._gallery_vm.toggle_favorite_row(index.row())

    def sync_selection(self, row: int) -> None:
        """Sync the grid selection when the playback asset changes."""

        try:
            index = self._asset_list_vm.index(row, 0)
            if not index.isValid():
                return
            selection_model = self._window.ui.grid_view.selectionModel()
            if selection_model is None:
                return
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            self._window.ui.grid_view.scrollTo(index)
        except Exception:
            self._logger.debug("sync_selection failed for row %s", row, exc_info=True)


__all__ = ["GalleryInteractionCoordinator"]
