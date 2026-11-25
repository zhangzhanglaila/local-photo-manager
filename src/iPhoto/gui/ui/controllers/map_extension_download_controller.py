"""Shared UI controller for downloading and activating the map extension."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, QThreadPool, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadRequest,
    MapExtensionDownloadResult,
    MapExtensionDownloadWorker,
)
from maps.map_sources import (
    apply_pending_osmand_extension_install,
    default_osmand_extension_root,
    default_pending_osmand_extension_root,
    has_installed_osmand_extension,
    has_pending_osmand_extension_install,
    supports_map_extension_download,
    verify_osmand_extension_install,
)

LOGGER = logging.getLogger(__name__)

# Two entry points (startup prompt and the settings action) each own a controller, so a
# single download could open two dialogs and show two progress bars. This holds the
# controller that is currently downloading so the other one can just raise its dialog.
_ACTIVE_DOWNLOAD = None
_SHOW_STARTUP_PROMPT_KEY = "ui.show_map_extension_startup_prompt"


class _MapExtensionProgressDialog(QDialog):
    """Modal progress dialog used by every map-extension entry point."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map Extension")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(420)
        self._allow_close = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._message_label = QLabel("Preparing map extension download...", self)
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._status_label = QLabel("", self)
        footer.addWidget(self._status_label)
        layout.addLayout(footer)

    def update_progress(self, current: int, total: int, message: str) -> None:
        self._message_label.setText(message)
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(max(0, min(current, total)))
            percent = int(round((current / total) * 100.0)) if total else 0
            self._status_label.setText(f"{percent}%")
        else:
            self._progress_bar.setRange(0, 0)
            self._status_label.clear()

    def allow_close(self) -> None:
        self._allow_close = True

    def closeEvent(self, event) -> None:
        if not self._allow_close:
            event.ignore()
            return
        super().closeEvent(event)


class MapExtensionDownloadController:
    """Coordinate startup prompts, download progress, and restart requests."""

    def __init__(
        self,
        parent: QWidget,
        context: RuntimeEntryContract,
        *,
        package_root: Path,
    ) -> None:
        self._parent = parent
        self._context = context
        self._package_root = Path(package_root).resolve()
        self._progress_dialog: _MapExtensionProgressDialog | None = None
        self._download_inflight = False
        global _ACTIVE_DOWNLOAD
        if _ACTIVE_DOWNLOAD is self:
            _ACTIVE_DOWNLOAD = None
        self._latest_result: MapExtensionDownloadResult | None = None
        self._active_worker: MapExtensionDownloadWorker | None = None
        self._temporarily_hidden_windows: list[QWidget] = []

    def maybe_prompt_on_startup(self) -> None:
        if not supports_map_extension_download():
            return
        if has_installed_osmand_extension(self._package_root):
            return
        if has_pending_osmand_extension_install(self._package_root):
            self._recover_pending_install_on_startup()
            return
        if not bool(self._context.settings.get(_SHOW_STARTUP_PROMPT_KEY, True)):
            return

        dialog = dialogs.MessageDialog(
            self._parent,
            title="Map Extension",
            message=(
                "Download the offline map extension now?\n\n"
                "The map extension enables the bundled offline OsmAnd map runtime."
            ),
            buttons=[("Not Now", "later"), ("Download", "download")],
            checkbox_text="Do not show again",
        )
        dialog.exec()

        if dialog.result_key == "download":
            self.start_download(source="startup")
            return

        if dialog.checkbox_checked:
            self._context.settings.set(_SHOW_STARTUP_PROMPT_KEY, False)

    def set_package_root(self, package_root: Path | None) -> None:
        """Update the active maps package root used for prompt/download checks."""

        if package_root is None:
            return
        self._package_root = Path(package_root).resolve()

    def start_download(self, *, source: str) -> None:
        del source
        global _ACTIVE_DOWNLOAD
        if (
            _ACTIVE_DOWNLOAD is not None
            and _ACTIVE_DOWNLOAD is not self
            and _ACTIVE_DOWNLOAD._download_inflight
        ):
            other = _ACTIVE_DOWNLOAD._progress_dialog
            if other is not None:
                other.raise_()
                other.activateWindow()
            return
        if self._download_inflight:
            if self._progress_dialog is not None:
                self._progress_dialog.raise_()
                self._progress_dialog.activateWindow()
            return

        # The map can be fully usable from an extension that is already on disk while the
        # menu still offered a download. Check both the controller's root and the
        # canonical package root — they resolved differently, which made a complete
        # install look missing. (The name is imported at module level; a local import
        # here used the wrong relative depth and silently disabled this guard.)
        if has_installed_osmand_extension(self._package_root) or (
            has_installed_osmand_extension(None)
        ):
            dialogs.show_information(
                self._parent,
                "The offline map extension is already installed.",
                title="Map Extension",
            )
            return

        if not supports_map_extension_download():
            dialogs.show_warning(
                self._parent,
                "Map extension downloads are unavailable on this platform.",
                title="Map Extension",
            )
            return

        self._download_inflight = True
        _ACTIVE_DOWNLOAD = self
        self._latest_result = None
        self._hide_blocking_top_level_windows()
        self._progress_dialog = _MapExtensionProgressDialog(self._parent)
        self._progress_dialog.show()
        self._progress_dialog.raise_()
        self._progress_dialog.activateWindow()

        worker = MapExtensionDownloadWorker(
            MapExtensionDownloadRequest(
                package_root=self._package_root,
                platform=sys.platform,
            )
        )
        worker.signals.progress.connect(self._handle_progress)
        worker.signals.ready.connect(self._handle_ready)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(self._handle_finished)
        self._active_worker = worker
        QThreadPool.globalInstance().start(worker, -1)

    def _handle_progress(self, current: int, total: int, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(int(current), int(total), str(message))

    def _handle_ready(self, result: object) -> None:
        if isinstance(result, MapExtensionDownloadResult):
            self._latest_result = result
        install_verified = verify_osmand_extension_install(self._package_root, platform=sys.platform)
        if not install_verified:
            self._handle_error(
                self._install_verification_failed_message(
                    "Map extension download finished, but the install folder was not renamed successfully."
                )
            )
            return
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(100, 100, "Map extension is installed. Restart required.")
            self._progress_dialog.allow_close()
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None

        restart_now = dialogs.confirm_action(
            self._parent,
            "Map extension download finished. Restart now to activate it?",
            title="Restart Required",
            yes_label="立即重启",
            no_label="稍后",
        )
        if not restart_now:
            self._restore_temporarily_hidden_windows()
        else:
            self._restart_application()

    def _handle_error(self, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.allow_close()
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        self._restore_temporarily_hidden_windows()
        dialogs.show_error(
            self._parent,
            str(message) or "Failed to download the map extension.",
            title="Map Extension",
        )

    def _handle_finished(self) -> None:
        self._download_inflight = False
        global _ACTIVE_DOWNLOAD
        if _ACTIVE_DOWNLOAD is self:
            _ACTIVE_DOWNLOAD = None
        self._active_worker = None

    def _recover_pending_install_on_startup(self) -> None:
        try:
            apply_pending_osmand_extension_install(self._package_root)
        except Exception:
            LOGGER.warning("Failed to recover pending map extension install", exc_info=True)
            dialogs.show_error(
                self._parent,
                self._install_verification_failed_message(
                    "A pending map extension install exists, but it could not be activated."
                ),
                title="Map Extension",
            )
            return

        if verify_osmand_extension_install(self._package_root, platform=sys.platform):
            dialogs.show_information(
                self._parent,
                "Map extension installation was completed. Restart the application to activate it.",
                title="Map Extension",
            )
            return

        dialogs.show_error(
            self._parent,
            self._install_verification_failed_message(
                "A pending map extension install was found, but the installed files could not be verified."
            ),
            title="Map Extension",
        )

    def _install_verification_failed_message(self, prefix: str) -> str:
        pending_root = default_pending_osmand_extension_root(self._package_root)
        extension_root = default_osmand_extension_root(self._package_root)
        return (
            f"{prefix}\n\n"
            f"Pending folder: {pending_root}\n"
            f"Active extension folder: {extension_root}"
        )

    def _restart_application(self) -> None:
        app = QCoreApplication.instance()
        if app is None:
            self._restore_temporarily_hidden_windows()
            return

        program, arguments = self._restart_command(app)
        if not QProcess.startDetached(program, arguments):
            self._restore_temporarily_hidden_windows()
            dialogs.show_error(
                self._parent,
                "Failed to relaunch the application automatically. Please restart it manually.",
                title="Restart Failed",
            )
            return

        window = self._parent.window()
        if window is not None:
            window.close()
        else:
            app.quit()

    def _restart_command(self, app: QCoreApplication) -> tuple[str, list[str]]:
        if getattr(sys, "frozen", False):
            program = app.applicationFilePath()
            arguments = list(sys.argv[1:])
            return program, arguments
        return sys.executable, list(sys.argv)

    def _hide_blocking_top_level_windows(self) -> None:
        self._temporarily_hidden_windows.clear()
        owner = self._parent.window()
        if owner is None:
            return

        for widget in QApplication.topLevelWidgets():
            if widget is owner or widget is self._progress_dialog:
                continue
            if widget.parentWidget() is not owner:
                continue
            if not widget.isVisible():
                continue
            if not bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint):
                continue
            self._temporarily_hidden_windows.append(widget)
            widget.hide()

    def _restore_temporarily_hidden_windows(self) -> None:
        while self._temporarily_hidden_windows:
            widget = self._temporarily_hidden_windows.pop()
            try:
                widget.show()
                widget.raise_()
            except RuntimeError:
                continue


__all__ = ["MapExtensionDownloadController"]
