"""Dialog orchestration helpers for the main window."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget

from typing import TYPE_CHECKING
from ....application.contracts.runtime_entry_contract import RuntimeEntryContract
from ....errors import LibraryError
from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from ....utils.pathutils import resolve_work_dir, get_custom_workspace_dir
from ..widgets import dialogs
from ....i18n import tr

if TYPE_CHECKING:
    from ..widgets.chrome_status_bar import ChromeStatusBar

_logger = logging.getLogger(__name__)


class DialogController:
    """Centralise dialog and message interactions."""

    def __init__(
        self,
        parent: QWidget,
        context: RuntimeEntryContract,
        status_bar: ChromeStatusBar,
    ) -> None:
        self._parent = parent
        self._context = context
        self._status = status_bar

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def open_album_dialog(self) -> Optional[Path]:
        # Start where the photos are, not in the process working directory: an empty
        # start path made the file dialog open the repository folder.
        return dialogs.select_directory(
            self._parent,
            tr("dialog.select_album"),
            start=self._dialog_start_directory(),
        )

    def _dialog_start_directory(self) -> Optional[Path]:
        """Return a sensible directory for folder pickers."""

        root = self._context.library.root()
        if root is not None and Path(root).is_dir():
            return Path(root)
        pictures = Path.home() / "Pictures"
        return pictures if pictures.is_dir() else Path.home()

    def bind_library_dialog(self) -> Optional[Path]:
        root = dialogs.select_directory(
            self._parent,
            tr("dialog.select_basic_library"),
            start=self._dialog_start_directory(),
        )
        if root is None:
            _logger.info("bind_library_dialog: user cancelled folder selection")
            return None
        _logger.info("bind_library_dialog: user selected folder %s", root)

        current_root = self._context.library.root()

        # If a library is already bound, check whether the selection is a
        # subdirectory or an existing root before deciding what to do.
        if current_root is not None:
            try:
                resolved_root = root.resolve()
                resolved_current = current_root.resolve()
            except OSError:
                resolved_root = root
                resolved_current = current_root

            # Subdirectory of existing root — just trigger a rescan.
            is_subdir = resolved_root != resolved_current and str(resolved_root).startswith(
                str(resolved_current) + os.sep
            )
            if is_subdir:
                _logger.info(
                    "bind_library_dialog: %s is under existing root %s, triggering rescan",
                    root,
                    current_root,
                )
                self._start_scan_if_needed(current_root)
                self._status.showMessage(tr("dialog.scanning_new_folder", name=root.name))
                return current_root

            # Already a bound root — check if database exists before deciding what to do.
            existing_roots = self._context.library.roots()
            _logger.info(
                "bind_library_dialog: checking %s against existing roots %s",
                root, [str(r) for r in existing_roots],
            )
            for existing in existing_roots:
                if self._path_equal(root, existing):
                    _logger.info(
                        "bind_library_dialog: %s matches existing root %s",
                        root, existing,
                    )
                    # ⭐ 修复：检查数据库是否存在，如果不存在则触发扫描
                    if self._should_rescan_library(existing):
                        _logger.info("bind_library_dialog: database missing, triggering rescan for %s", existing)
                        self._start_scan_if_needed(existing)
                        self._status.showMessage(tr("dialog.rescanning_library_folder", name=root.name))
                    else:
                        self._status.showMessage(
                            tr("dialog.already_library_folder", root=root)
                        )
                    return root

            # Add as an additional root (coexist with existing libraries).
            try:
                self._context.library.add_root(root)
                _logger.info("bind_library_dialog: added extra root %s", root)
            except Exception as exc:
                _logger.error("bind_library_dialog: add_root failed: %s", exc)
                dialogs.show_error(self._parent, str(exc))
                return None

            self._persist_library_paths()
            self._start_scan_if_needed(root)
            self._status.showMessage(tr("dialog.added_library_folder", name=root.name))
            return root

        # No existing library — open as the primary root.
        try:
            self._context.open_library(root)
            _logger.info("bind_library_dialog: open_library succeeded, root=%s", self._context.library.root())
        except LibraryError as exc:
            _logger.error("bind_library_dialog: open_library failed: %s", exc)
            dialogs.show_error(self._parent, str(exc))
            return None
        bound_root = self._context.library.root()
        if bound_root is not None:
            self._persist_library_paths()
            self._start_initial_scan_if_needed(bound_root)
            self._status.showMessage(tr("dialog.basic_library_bound", root=bound_root))
            try:
                self._context.facade.open_album(bound_root)
                _logger.info("bind_library_dialog: facade.open_album succeeded")
            except Exception:
                _logger.exception("bind_library_dialog: facade.open_album failed")
            sidebar = getattr(getattr(self._parent, "ui", None), "sidebar", None)
            if sidebar is not None:
                _logger.info("bind_library_dialog: selecting All Photos in sidebar")
                sidebar.select_all_photos(emit_signal=True)
            else:
                _logger.warning("bind_library_dialog: sidebar not found on parent")
        else:
            _logger.warning("bind_library_dialog: library.root() is None after open_library")
        return bound_root

    @staticmethod
    def _path_equal(a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return a == b

    def _should_rescan_library(self, root: Path) -> bool:
        """Return True when the library index is missing, empty or unreadable."""

        from ....cache.index_store import IndexStore, index_db_path

        if not index_db_path(root).exists():
            return True
        store = IndexStore(root)
        try:
            return store.count() == 0
        except Exception:  # noqa: BLE001 - a corrupt database means "rescan"
            return True
        finally:
            store.close()

    def _persist_library_paths(self) -> None:
        """Save all bound library roots to settings."""
        roots = self._context.library.roots()
        self._context.settings.set("basic_library_paths", [str(r) for r in roots])
        # Keep legacy key in sync with the primary root.
        if roots:
            self._context.settings.set("basic_library_path", str(roots[0]))

    def _start_initial_scan_if_needed(self, bound_root: Path) -> None:
        from ....cache.index_store import index_db_path

        if index_db_path(bound_root).exists():
            return
        self._start_scan_if_needed(bound_root)

    def _start_scan_if_needed(self, bound_root: Path) -> None:
        if self._context.library.is_scanning_path(bound_root):
            return
        self._context.facade.scan_root_async(
            bound_root,
            include=DEFAULT_INCLUDE,
            exclude=DEFAULT_EXCLUDE,
        )

    def show_error(self, message: str) -> None:
        dialogs.show_error(self._parent, message)

    def prompt_for_basic_library(self) -> None:
        dialogs.show_information(
            self._parent,
            tr("dialog.prompt_bind_library"),
            title=tr("dialog.bind_basic_library"),
        )
        self.bind_library_dialog()

    def prompt_restore_to_root(self, filename: str) -> bool:
        """Ask whether *filename* should be restored to the library root."""

        message = tr("dialog.restore_to_root_msg", name=filename)
        return dialogs.confirm_action(
            self._parent,
            message,
            title=tr("dialog.restore_failed"),
            yes_label=tr("dialog.yes"),
            no_label=tr("dialog.no"),
        )
