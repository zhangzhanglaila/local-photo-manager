"""Opening a library/album and switching the workspace, outside MainCoordinator.

Opening an album from the dashboard, the open dialog or a command-line path all
need the same three steps: make sure a library session exists for that path,
tell the navigation coordinator to open it, and report timing. Workspace
relocation and the setup wizard are the adjacent library-level entry points.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject


class LibraryOpenCoordinator(QObject):
    """Open albums/libraries and run the workspace-level entry points."""

    def __init__(
        self,
        window,
        context,
        *,
        dialog,
        navigation,
        status_bar,
        facade,
        library_root_getter,
        on_library_changed,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._dialog = dialog
        self._navigation = navigation
        self._status_bar = status_bar
        self._facade = facade
        self._library_root = library_root_getter
        self._on_library_changed = on_library_changed
        self._logger = logging.getLogger(__name__)

    def run_setup_wizard(self) -> None:
        """Re-open the first-run setup wizard from the settings menu."""

        self._context.show_welcome_wizard()

    def change_workspace(self) -> None:
        """Copy the workspace to a new folder, then switch the setting."""

        from iPhoto.gui.ui.controllers.workspace_relocation_controller import (
            relocate_workspace_interactively,
        )

        root = self._context.library.root()
        changed = relocate_workspace_interactively(
            self._window, root, self._context.settings
        )
        if changed:
            self._status_bar.show_message("存储位置已更改，请重启应用以生效。", 8000)

    def handle_open_album_dialog(self) -> None:
        path = self._dialog.open_album_dialog()
        if path:
            self.open_album_from_path(path)

    def open_album_from_path(self, path: Path) -> None:
        """Open *path*, creating a library session first when needed."""

        target = Path(path).expanduser()
        self._logger.info("open_album_from_path: start %s", target)
        started = time.monotonic()
        if not self._ensure_session_for_open_album(target):
            self._logger.info(
                "open_album_from_path: session failed (%.2fs)",
                time.monotonic() - started,
            )
            return
        self._logger.info(
            "open_album_from_path: session ok (%.2fs), opening album...",
            time.monotonic() - started,
        )
        album_started = time.monotonic()
        self._navigation.open_album(target)
        self._logger.info(
            "open_album_from_path: album opened (%.2fs), total=%.2fs",
            time.monotonic() - album_started,
            time.monotonic() - started,
        )

    def _ensure_session_for_open_album(self, path: Path) -> bool:
        """Ensure standalone album opens have a session-bound query surface."""

        if not path.exists() or not path.is_dir():
            return True

        current_root = self._library_root()
        if current_root is not None and self._path_is_descendant(path, current_root):
            return True

        open_library = getattr(self._context, "open_library", None)
        if not callable(open_library):
            return True

        try:
            open_library(path)
        except Exception as exc:
            self._facade.errorRaised.emit(str(exc))
            return False

        self._on_library_changed()
        return True

    @staticmethod
    def _path_is_descendant(path: Path, root: Path) -> bool:
        try:
            Path(path).resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return False
        return True


__all__ = ["LibraryOpenCoordinator"]
