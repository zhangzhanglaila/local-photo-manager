"""Grid interaction and foreground-scan yielding, independent of window assembly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="QTimer needs a Qt application")

from iPhoto.gui.coordinators.gallery_interaction_coordinator import (  # noqa: E402
    GalleryInteractionCoordinator,
)


class _Library:
    def __init__(self) -> None:
        self.registered = 0
        self.unregistered = 0

    def register_foreground_viewing(self) -> None:
        self.registered += 1

    def unregister_foreground_viewing(self) -> None:
        self.unregistered += 1


class _Dialog:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class _Updates:
    def __init__(self, root) -> None:
        self.root = root
        self.handled: list[object] = []

    def handle_media_load_failure(self, path):
        self.handled.append(path)
        return self.root


class _Gallery:
    def __init__(self) -> None:
        self.opened: list[int] = []
        self.favorites: list[int] = []
        self.reloaded = 0

    def open_row(self, row: int) -> None:
        self.opened.append(row)

    def toggle_favorite_row(self, row: int) -> None:
        self.favorites.append(row)

    def reload_current_selection(self) -> None:
        self.reloaded += 1


class _SelectionController:
    def __init__(self, active: bool = False) -> None:
        self._active = active

    def is_active(self) -> bool:
        return self._active


class _SelectionModel:
    def __init__(self) -> None:
        self.current = None

    def setCurrentIndex(self, index, flags) -> None:
        self.current = (index, flags)


class _GridView:
    def __init__(self) -> None:
        self.selection = _SelectionModel()
        self.scrolled = []

    def selectionModel(self):
        return self.selection

    def scrollTo(self, index) -> None:
        self.scrolled.append(index)


class _ModelIndex:
    def __init__(self, row: int, valid: bool = True) -> None:
        self._row = row
        self._valid = valid

    def isValid(self) -> bool:
        return self._valid

    def row(self) -> int:
        return self._row


class _Model:
    def __init__(self) -> None:
        self.indexes: list[int] = []

    def index(self, row: int, column: int):
        self.indexes.append(row)
        return _ModelIndex(row)


def _make(*, selection_active: bool = False, refresh_root=None):
    library = _Library()
    gallery = _Gallery()
    dialog = _Dialog()
    updates = _Updates(refresh_root)
    facade = SimpleNamespace(library_updates=updates)
    grid = _GridView()
    window = SimpleNamespace(ui=SimpleNamespace(grid_view=grid))
    model = _Model()
    coordinator = GalleryInteractionCoordinator(
        window,
        SimpleNamespace(library=library),
        gallery_vm=gallery,
        gallery_store=gallery,
        asset_list_vm=model,
        dialog=dialog,
        facade=facade,
        selection_controller=_SelectionController(selection_active),
    )
    return coordinator, library, gallery, dialog, updates, grid, model


def test_foreground_registration_is_balanced() -> None:
    coordinator, library, _gallery, _dialog, _updates, _grid, _model = _make()

    coordinator.enter_foreground_viewing()
    coordinator.leave_foreground_viewing()
    coordinator.on_video_playback_state_changed(True)
    coordinator.on_video_playback_state_changed(False)

    assert (library.registered, library.unregistered) == (2, 2)


def test_scrolling_registers_once_and_idle_releases() -> None:
    coordinator, library, _gallery, _dialog, _updates, _grid, _model = _make()

    coordinator.on_gallery_scrolled(0, 49)
    coordinator.on_gallery_scrolled(10, 59)
    assert library.registered == 1

    coordinator.on_scroll_idle()
    assert library.unregistered == 1

    coordinator.on_scroll_idle()
    assert library.unregistered == 1


def test_media_failure_dialog_blocks_reentrant_failures() -> None:
    coordinator, _library, gallery, dialog, updates, _grid, _model = _make(
        refresh_root=object()
    )
    nested: list[bool] = []

    original_show_error = dialog.show_error

    def show_error(message: str) -> None:
        original_show_error(message)
        if not nested:
            nested.append(True)
            coordinator.on_media_load_failed(Path("b.jpg"), "second")

    dialog.show_error = show_error  # type: ignore[method-assign]

    coordinator.on_media_load_failed(Path("a.jpg"), "broken")

    assert len(dialog.errors) == 1
    assert updates.handled == [Path("a.jpg")]

    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    assert gallery.reloaded == 1


def test_asset_and_favorite_clicks_reach_the_gallery() -> None:
    coordinator, _library, gallery, _dialog, _updates, _grid, _model = _make()

    coordinator.on_asset_clicked(_ModelIndex(3))
    coordinator.on_favorite_clicked(_ModelIndex(4))

    assert gallery.opened == [3]
    assert gallery.favorites == [4]


def test_clicks_are_ignored_while_selection_mode_is_active() -> None:
    coordinator, _library, gallery, _dialog, _updates, _grid, _model = _make(
        selection_active=True
    )

    coordinator.on_asset_clicked(_ModelIndex(3))

    assert gallery.opened == []


def test_sync_selection_selects_and_scrolls() -> None:
    coordinator, _library, _gallery, _dialog, _updates, grid, model = _make()

    coordinator.sync_selection(2)

    assert model.indexes == [2]
    assert grid.selection.current is not None
    assert grid.scrolled

