"""Status-bar, theme and language wiring for the main window.

``MainCoordinator`` used to contain every facade/status-bar connection plus the
theme and language menus. That is pure window chrome: it translates signals into
existing panel slots and writes two settings keys. The search-model sync and the
move-finished toast stay with their owners and are passed in as callbacks.
"""

from __future__ import annotations

from PySide6.QtCore import QObject


class WindowChromeCoordinator(QObject):
    """Wire the status bar, theme menu and language menu."""

    def __init__(
        self,
        window,
        context,
        *,
        facade,
        status_bar,
        dialog,
        on_move_finished,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._facade = facade
        self._status_bar = status_bar
        self._dialog = dialog
        self._on_move_finished = on_move_finished

    def connect(self) -> None:
        """Install all chrome signals; called once after the coordinators exist."""

        ui = self._window.ui
        updates = self._facade.library_updates

        # Facade signals -> status bar panels.
        updates.scanProgress.connect(self._status_bar.handle_scan_progress)
        updates.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._facade.scanBatchFailed.connect(self._status_bar.handle_scan_batch_failed)
        self._facade.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._facade.scanFinished.connect(self._status_bar.handle_scan_finished)

        self._facade.loadStarted.connect(self._status_bar.handle_load_started)
        self._facade.loadProgress.connect(self._status_bar.handle_load_progress)
        self._facade.loadFinished.connect(self._status_bar.handle_load_finished)

        # After the first scan completes, allow "No media found" to show.
        self._facade.scanProgress.connect(lambda *_: ui.grid_view.set_scan_started())
        self._context.library.scanProgress.connect(
            lambda *_: ui.grid_view.set_scan_started()
        )
        self._facade.scanFinished.connect(lambda *_: ui.grid_view.set_scan_completed())
        self._context.library.scanFinished.connect(
            lambda *_: ui.grid_view.set_scan_completed()
        )

        import_service = self._facade.import_service
        import_service.importStarted.connect(self._status_bar.handle_import_started)
        import_service.importProgress.connect(self._status_bar.handle_import_progress)
        import_service.importFinished.connect(self._status_bar.handle_import_finished)

        move_service = self._facade.move_service
        move_service.moveStarted.connect(self._status_bar.handle_move_started)
        move_service.moveProgress.connect(self._status_bar.handle_move_progress)
        move_service.moveFinished.connect(self._status_bar.handle_move_finished)
        move_service.moveFinished.connect(self._on_move_finished)

        # Error reporting.
        self._facade.errorRaised.connect(self._dialog.show_error)
        self._context.library.errorRaised.connect(self._dialog.show_error)

        # Theme switching.
        ui.theme_system.triggered.connect(
            lambda: self._context.settings.set("ui.theme", "system")
        )
        ui.theme_light.triggered.connect(
            lambda: self._context.settings.set("ui.theme", "light")
        )
        ui.theme_dark.triggered.connect(
            lambda: self._context.settings.set("ui.theme", "dark")
        )

        current_theme = self._context.settings.get("ui.theme", "system")
        if current_theme == "light":
            ui.theme_light.setChecked(True)
        elif current_theme == "dark":
            ui.theme_dark.setChecked(True)
        else:
            ui.theme_system.setChecked(True)

        # Language switching.
        ui.language_zh.triggered.connect(
            lambda: self._context.settings.set("ui.language", "zh")
        )
        ui.language_en.triggered.connect(
            lambda: self._context.settings.set("ui.language", "en")
        )
        current_lang = self._context.settings.get("ui.language", "zh")
        if current_lang == "en":
            ui.language_en.setChecked(True)
        else:
            ui.language_zh.setChecked(True)

        self._context.language.languageChanged.connect(
            lambda _lang: ui.main_header.retranslate()
        )
        self._context.language.languageChanged.connect(
            lambda _lang: ui.sidebar.retranslate()
        )
        self._context.language.languageChanged.connect(
            lambda _lang: ui.grid_view.retranslate()
        )


__all__ = ["WindowChromeCoordinator"]
