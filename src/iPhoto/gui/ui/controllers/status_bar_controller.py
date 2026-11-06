"""Helpers responsible for status-bar progress feedback."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from typing import TYPE_CHECKING
from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QProgressBar

from ....application.contracts.runtime_entry_contract import RuntimeEntryContract
from ....config import RECENTLY_DELETED_DIR_NAME
from ....i18n import tr
from ....utils.pathutils import paths_equal

if TYPE_CHECKING:
    from ..widgets.chrome_status_bar import ChromeStatusBar

class StatusBarController(QObject):
    """Manage progress feedback and transient messages in the status bar."""

    def __init__(
        self,
        status_bar: ChromeStatusBar,
        progress_bar: QProgressBar,
        rescan_action: QAction | None,
        context: RuntimeEntryContract,
    ) -> None:
        super().__init__(status_bar)
        self._status_bar = status_bar
        self._progress_bar = progress_bar
        self._rescan_action = rescan_action
        self._progress_context: Optional[str] = None
        # ``_move_context_delete`` keeps track of whether the current move feedback refers
        # to a deletion into Recently Deleted so we can surface "Delete" specific copy.
        self._move_context_delete: bool = False
        # ``_move_context_restore`` mirrors restore operations so that progress updates
        # can use "Restore" focused language.
        self._move_context_restore: bool = False
        self._context = context

    # Generic helpers -------------------------------------------------
    def show_message(self, message: str, timeout_ms: int | None = None) -> None:
        """Proxy :meth:`QStatusBar.showMessage` for the owning controller."""

        if timeout_ms is None:
            self._status_bar.showMessage(message)
        else:
            self._status_bar.showMessage(message, timeout_ms)

    def begin_scan(self) -> None:
        """Prepare the UI for a long-running scan operation."""

        # ⭐ 修复：无论当前状态如何，都应该允许开始扫描
        self._progress_context = "scan"
        self._status_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        if self._rescan_action is not None:
            self._rescan_action.setEnabled(False)
            self._rescan_action.setText(tr("status.scanning"))
        self.show_message(tr("status.scan_started"))

    # Facade callbacks ------------------------------------------------
    def handle_scan_progress(self, root: Path, current: int, total: int) -> None:
        """Update the progress bar while the library is being scanned."""

        # ⭐ 修复：允许在任何上下文中显示扫描进度
        # 即使当前处于 "load" 或其他上下文，扫描进度也应该显示
        # 因为扫描是后台操作，不应该被 UI 状态阻塞
        if self._progress_context is None:
            # A scan triggered from outside the controller started without
            # calling :meth:`begin_scan`; bootstrap the UI lazily.
            self.begin_scan()
        elif self._progress_context not in {"scan", None}:
            # ⭐ 修复：强制切换到扫描上下文以显示进度
            # 其他操作（如加载）应该让位于扫描进度显示
            self._progress_context = "scan"

        if total <= 0:
            # total < 0  → discovery thread hasn't started counting yet
            # total == 0, current == 0 → discovery just started, no files found *yet*
            # In both cases show the generic "counting" message.  The
            # "no files" case is handled by handle_scan_finished instead.
            self._progress_bar.setRange(0, 0)
            self.show_message(tr("status.scanning_counting"))
        else:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(max(0, min(current, total)))
            percent = int(max(0, min(current, total)) * 100 // total) if total else 0
            self.show_message(
                tr(
                    "status.scanning_progress",
                    percent=percent,
                    current=current,
                    total=total,
                )
            )
        self._progress_bar.setVisible(True)

    def handle_scan_finished(self, _root: Path, success: bool) -> None:
        """Restore the status bar once a scan completes."""

        # ``_root`` is guaranteed to be a :class:`Path` because the facade now
        # emits strongly typed signals.  The argument is intentionally unused
        # because the status bar only cares about the outcome of the scan.

        if self._progress_context == "scan":
            self._progress_bar.setVisible(False)
            self._progress_bar.setRange(0, 0)
            self._progress_context = None
        if self._rescan_action is not None:
            self._rescan_action.setEnabled(True)
            self._rescan_action.setText(tr("action.rescan"))
        message = tr("status.scan_complete") if success else tr("status.scan_failed")
        self.show_message(message, 5000)

    def handle_scan_batch_failed(self, _root: Path, count: int) -> None:
        """Report a partial failure without interrupting the active scan."""
        self.show_message(tr("status.scan_batch_failed", count=count), 5000)

    def handle_load_started(self, root: Path) -> None:
        """Show an indeterminate progress indicator while assets load."""

        self._progress_context = "load"
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self.show_message(tr("status.loading_items"))

    def handle_load_progress(self, root: Path, current: int, total: int) -> None:
        """Update the progress bar while assets stream into the model."""

        if self._progress_context != "load":
            return
        if total <= 0:
            self._progress_bar.setRange(0, 0)
        else:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(max(0, min(current, total)))
        if total > 0:
            self.show_message(tr("status.loading_progress", current=current, total=total))

    def handle_load_finished(self, root: Path, success: bool) -> None:
        """Hide the progress bar once loading wraps up."""

        if self._progress_context != "load":
            return
        self._progress_bar.setVisible(False)
        self._progress_bar.setRange(0, 0)
        self._progress_context = None
        message = tr("status.load_complete") if success else tr("status.load_failed")
        self.show_message(message, 5000)

    def handle_import_started(self, root: Path) -> None:
        """Display an indeterminate indicator when an import begins."""

        # The import workflow mirrors the rescan feedback so we reuse the same
        # progress bar widget.  Setting the context allows other handlers to
        # recognise that the UI is currently occupied with an import task.
        self._progress_context = "import"
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self.show_message(tr("status.import_started"))

    def handle_import_progress(self, root: Path, current: int, total: int) -> None:
        """Update the progress bar while the worker copies files."""

        if self._progress_context != "import":
            return
        if total <= 0:
            # Keep the bar indeterminate when the worker cannot determine how
            # many items remain (for example during the initial copy).
            self._progress_bar.setRange(0, 0)
        else:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(max(0, min(current, total)))
        if 0 < current < total:
            self.show_message(tr("status.import_progress", current=current, total=total))
        elif total > 0 and current >= total:
            # The worker emits a final update once all files are copied to let
            # the user know that the subsequent rescan is in progress.
            self.show_message(tr("status.import_rescanning"))

    def handle_import_finished(self, root: Path | None, success: bool, message: str) -> None:
        """Reset the status bar once the import worker signals completion."""

        if self._progress_context == "import":
            self._progress_bar.setVisible(False)
            self._progress_bar.setRange(0, 0)
            self._progress_context = None
        self.show_message(message, 5000)

    def handle_move_started(self, source: Path, destination: Path) -> None:
        """Display an indeterminate indicator while files are being moved."""

        self._progress_context = "move"
        trash_root = self._context.library.deleted_directory()
        if trash_root is None:
            self._move_context_delete = destination.name == RECENTLY_DELETED_DIR_NAME
            self._move_context_restore = source.name == RECENTLY_DELETED_DIR_NAME
        else:
            self._move_context_delete = self._paths_equal(destination, trash_root)
            self._move_context_restore = (
                not self._move_context_delete
                and self._paths_equal(source, trash_root)
            )
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        if self._move_context_delete:
            message = tr("status.delete_started")
        elif self._move_context_restore:
            message = tr("status.restore_started")
        else:
            message = tr("status.move_started")
        self.show_message(message)

    def handle_move_progress(self, _source: Path, current: int, total: int) -> None:
        """Update the progress bar while the move worker processes files."""

        if self._progress_context != "move":
            return
        if total <= 0:
            self._progress_bar.setRange(0, 0)
        else:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(max(0, min(current, total)))
        if 0 < current < total:
            if self._move_context_delete:
                msg_key = "status.delete_progress"
            elif self._move_context_restore:
                msg_key = "status.restore_progress"
            else:
                msg_key = "status.move_progress"
            self.show_message(tr(msg_key, current=current, total=total))
        elif total > 0 and current >= total:
            if self._move_context_delete:
                self.show_message(tr("status.delete_rescanning"))
            elif self._move_context_restore:
                self.show_message(tr("status.restore_rescanning"))
            else:
                self.show_message(tr("status.move_rescanning"))

    def handle_move_finished(
        self,
        _source: Path,
        _destination: Path,
        _success: bool,
        message: str,
    ) -> None:
        """Hide the progress bar and surface the worker's completion message."""

        if self._progress_context == "move":
            self._progress_bar.setVisible(False)
            self._progress_bar.setRange(0, 0)
            self._progress_context = None
            self._move_context_delete = False
            self._move_context_restore = False
        if message:
            self.show_message(message, 5000)
        else:
            # When restores are skipped entirely we suppress the completion
            # toast to avoid flashing an empty outcome banner. Clearing the
            # message explicitly ensures the status text from the in-flight
            # progress updates does not linger in the bar.
            self._status_bar.clearMessage()

    def _paths_equal(self,first,second) -> bool:
        """Delegate to the single shared comparison."""

        return paths_equal(first,second)
