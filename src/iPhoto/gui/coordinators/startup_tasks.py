"""One-time startup checks that are not window assembly.

Clearing stale location names for (0,0) GPS assets and surfacing a missing
ExifTool binary are startup side effects. They need the window (for the warning
dialog) and the asset runtime (for the repository), not the coordinator's view
state.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject

from iPhoto.i18n import tr


class StartupTasks(QObject):
    """Run the one-time cleanup/warning checks at startup."""

    def __init__(self, window, context, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._logger = logging.getLogger(__name__)

    def run(self) -> None:
        self._wire_exiftool_missing_warning()
        self._cleanup_null_gps_locations()

    def _cleanup_null_gps_locations(self) -> None:
        """Clear persisted location names for (0, 0) GPS assets."""

        try:
            repo = getattr(self._context.asset_runtime, "repository", None)
            if repo is None:
                return
            clear_fn = getattr(repo, "clear_location_for_null_gps", None)
            if clear_fn is None:
                return
            count = clear_fn()
            if count > 0:
                self._logger.info(
                    "Cleared %d stale location entries for (0,0) GPS assets", count
                )
        except Exception:
            self._logger.debug("GPS location cleanup skipped", exc_info=True)

    def _wire_exiftool_missing_warning(self) -> None:
        """Show a one-time warning dialog if ExifTool is not found during scanning."""

        from iPhoto.infrastructure.services import metadata_provider
        from iPhoto.utils.exiftool import _resolve_exiftool_executable

        def _show_warning(message: str) -> None:
            from iPhoto.gui.ui.widgets import dialogs

            warning = tr("dialog.exiftool_warning", message=message)
            self._logger.warning("ExifTool not found, showing warning dialog")
            dialogs.show_warning(
                self._window, warning, title=tr("dialog.exiftool_not_found")
            )

        try:
            _resolve_exiftool_executable()
        except Exception as exc:
            metadata_provider._exiftool_missing_notified = True
            _show_warning(str(exc))

        metadata_provider._on_exiftool_missing = _show_warning


__all__ = ["StartupTasks"]
