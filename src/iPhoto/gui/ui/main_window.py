"""Qt widgets composing the main application window."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QMenuBar, QSystemTrayIcon

from ...application.contracts.runtime_entry_contract import RuntimeEntryContract

from .media import require_multimedia
from .ui_main_window import ChromeStatusBar, Ui_MainWindow
from .widgets.tray_icon import AppTrayIcon
from .window_manager import FramelessWindowManager
# MainController import removed; logic is now in MainCoordinator via self.coordinator

_logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Primary window for the desktop experience."""

    def __init__(self, context: RuntimeEntryContract) -> None:
        super().__init__()
        require_multimedia()

        self._context = context
        self.ui = Ui_MainWindow()

        # ``setupUi`` triggers a handful of ``QEvent`` instances while it
        # constructs child widgets.  Those events fire before we can build the
        # frameless chrome helper, so we predeclare the attribute to avoid
        # ``AttributeError`` during the early lifecycle.
        self.window_manager: FramelessWindowManager | None = None

        self.ui.setupUi(self, context.library)

        # ``FramelessWindowManager`` is responsible for every custom chrome
        # behaviour.  The main window therefore remains a thin container that
        # simply forwards lifecycle events to the helper.
        self.window_manager = FramelessWindowManager(self, self.ui)

        # The controller (now coordinator) is assigned via setter or set later.
        self.coordinator = None

        # Tray presence: the app keeps running when the window is hidden, and the
        # tray menu is how the window comes back (or how the app is quit).
        self._tray: AppTrayIcon | None = None
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if self._tray_available:
            self._tray = AppTrayIcon(self, self.windowIcon(), "iPhoto")
            self._tray.show_requested.connect(self.restore_from_tray)
            self._tray.settings_requested.connect(self.ui._open_settings)
            self._tray.quit_requested.connect(self.quit_from_tray)
            self._tray.show()
            _logger.info("tray icon created")
        else:
            _logger.info("system tray unavailable; minimising falls back to the taskbar")

        # Retain the behaviour where clicking the chrome gives the window focus
        # so global shortcuts continue to function when no child widget is
        # active.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def set_coordinator(self, coordinator):
        """Inject the MainCoordinator."""
        self.coordinator = coordinator
        # The window manager needs a reference to the 'controller' to handle immersive mode.
        # MainCoordinator implements the necessary interface.
        self.window_manager.set_controller(self.coordinator)

    # ------------------------------------------------------------------
    # QWidget overrides
    def closeEvent(self, event: QCloseEvent) -> None:
        """Ask whether to minimise or quit, then tear down background services."""

        if getattr(self, "_force_quit", False):
            self._teardown()
            super().closeEvent(event)
            return

        choice = self._ask_close_action()
        if choice == "minimise":
            event.ignore()
            self.minimise_to_tray()
            return
        if choice == "cancel":
            event.ignore()
            return

        self._force_quit = True
        self._teardown()
        super().closeEvent(event)
        QApplication.instance().quit()

    def minimise_to_tray(self) -> None:
        """Hide the window but keep the process (tray icon stays reachable)."""

        if self._tray is None:
            # No tray on this desktop: fall back to an ordinary minimised window
            # so the app cannot become unreachable.
            self.showMinimized()
            return
        self.hide()
        self._tray.show_message("iPhoto", "已最小化到托盘，右键托盘图标可退出。")

    def restore_from_tray(self) -> None:
        """Show and focus the window, restoring it if it was minimised."""

        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        """Quit through the normal close path so teardown still runs."""

        self._force_quit = True
        self.close()

    def _teardown(self) -> None:
        if self._tray is not None:
            self._tray.hide()
        if self.window_manager is not None:
            self.window_manager.cleanup()
        if self.coordinator:
            self.coordinator.shutdown()

    def _ask_close_action(self) -> str:
        """Return ``"minimise"``, ``"quit"`` or ``"cancel"``.

        A previously remembered choice skips the prompt entirely; the Settings
        window exposes the same preference so the choice stays reversible.
        """

        settings = getattr(self._context, "settings", None)
        remembered = None
        if settings is not None:
            try:
                remembered = settings.get("ui.close_action", None)
            except Exception:  # noqa: BLE001 - a bad value must not block closing
                remembered = None
        if remembered in {"minimise", "quit"}:
            return remembered

        from .widgets.close_dialog import CloseDialog

        dialog = CloseDialog(self)
        dialog.exec()
        choice = dialog.choice
        if (
            dialog.remember
            and choice in {"minimise", "quit"}
            and settings is not None
        ):
            try:
                settings.set("ui.close_action", choice)
            except Exception:  # noqa: BLE001 - persistence is best effort
                pass
        return choice

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # ``setupUi`` can emit a resize event before the frameless manager is
        # constructed.  Guard against that early call.
        if self.window_manager is not None:
            self.window_manager.handle_resize_event(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowIconChange and self._tray is not None:
            # The application icon is applied after this window is constructed.
            self._tray.set_icon(self.windowIcon())
        if self.window_manager is not None:
            self.window_manager.handle_change_event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Escape is handled by AppShortcutManager → exit_fullscreen (guarded).
        # Do NOT unconditionally close the window here.
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Window chrome accessors used by child widgets
    def statusBar(self) -> ChromeStatusBar:
        """Return the custom status bar embedded in the rounded shell."""

        return self.ui.status_bar

    def menuBar(self) -> QMenuBar:
        """Expose the menu bar hosted inside the rounded window shell."""

        if self.window_manager is None:
            return super().menuBar()
        return self.window_manager.menuBar()

    def menu_stylesheet(self) -> str | None:
        """Return the cached ``QMenu`` stylesheet so other widgets can reuse it."""

        if self.window_manager is None:
            return None
        return self.window_manager.menu_stylesheet()

    def get_qmenu_stylesheet(self) -> str | None:
        """Expose the rounded ``QMenu`` stylesheet, rebuilding it if necessary."""

        if self.window_manager is None:
            return None
        return self.window_manager.get_qmenu_stylesheet()

    # ------------------------------------------------------------------
    # Convenience wrappers kept for backwards compatibility
    def position_live_badge(self) -> None:
        """Allow legacy callers to reposition the Live badge."""

        if self.window_manager is not None:
            self.window_manager.position_live_badge()

    def position_resize_widgets(self) -> None:
        """Allow legacy callers to reposition the resize affordances."""

        if self.window_manager is not None:
            self.window_manager.position_resize_widgets()

    def toggle_fullscreen(self) -> None:
        """Toggle the immersive full screen mode."""

        if self.window_manager is not None:
            self.window_manager.toggle_fullscreen()

    def enter_fullscreen(self) -> None:
        """Expand the window into the immersive presentation mode."""

        if self.window_manager is not None:
            self.window_manager.enter_fullscreen()

    def exit_fullscreen(self) -> None:
        """Restore the standard chrome from immersive mode."""

        if self.window_manager is not None:
            self.window_manager.exit_fullscreen()

    # ------------------------------------------------------------------
    # Public API used by sidebar/actions
    def open_album_from_path(self, path: Path) -> None:
        """Expose navigation for legacy callers."""
        if self.coordinator:
            self.coordinator.open_album_from_path(path)

    def current_selection(self) -> list[Path]:
        """Return absolute paths for every asset selected in the active view."""

        # Priority 1: Grid View (Gallery)
        if self.ui.grid_view.selectionModel() is not None:
            grid_indexes = self.ui.grid_view.selectionModel().selectedIndexes()
            if grid_indexes:
                if self.coordinator:
                    return self.coordinator.paths_from_indexes(grid_indexes)

        # Priority 2: Filmstrip View
        if self.ui.filmstrip_view.selectionModel() is not None:
            indexes = self.ui.filmstrip_view.selectionModel().selectedIndexes()
            if indexes:
                if self.coordinator:
                    return self.coordinator.paths_from_indexes(indexes)

        return []

