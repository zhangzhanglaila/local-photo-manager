"""``LocationCoordinator`` focuses a photo on the map and returns to it.

The behaviour used to be ten methods on ``MainCoordinator`` plus two attributes;
it now has one owner, so these tests can drive it without a window.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.gui.coordinators import location_coordinator as module  # noqa: E402
from iPhoto.gui.coordinators.location_coordinator import (  # noqa: E402
    LocationCoordinator,
)


class _GalleryVm:
    def __init__(self) -> None:
        self.focused: list = []
        self.all_shown = 0
        self.opened_rows: list[int] = []

    def open_location_map_focused(self, asset) -> None:
        self.focused.append(asset)

    def show_all_location_assets(self) -> None:
        self.all_shown += 1

    def open_row(self, row: int) -> None:
        self.opened_rows.append(row)


class _Store:
    def __init__(self, rows: dict[Path, int] | None = None) -> None:
        self.rows = rows or {}

    def row_for_path(self, path: Path):
        return self.rows.get(path)


class _Navigation:
    def __init__(self) -> None:
        self.all_photos = 0
        self._pending_detail_return = None

    def open_all_photos(self) -> None:
        self.all_photos += 1


class _MapView:
    def __init__(self) -> None:
        self.focused_mode: list[bool] = []
        self.widget = None

    def set_focused_asset_mode(self, enabled: bool) -> None:
        self.focused_mode.append(enabled)

    def map_widget(self):
        return self.widget


class _Sidebar:
    def __init__(self) -> None:
        self.static_nodes: list[str] = []
        self._tree = SimpleNamespace(viewport=lambda: SimpleNamespace(update=lambda: None))

    def select_static_node(self, name: str) -> None:
        self.static_nodes.append(name)


class _Header:
    def __init__(self) -> None:
        self.widgets: list = []

    def layout(self):
        return SimpleNamespace(addWidget=self.widgets.append)


class _DetailVm:
    def __init__(self, presentation) -> None:
        self.presentation = SimpleNamespace(value=presentation)


def _make(*, presentation=None, rows=None):
    gallery = _GalleryVm()
    store = _Store(rows)
    navigation = _Navigation()
    ui = SimpleNamespace(
        map_view=_MapView(),
        sidebar=_Sidebar(),
        main_header=_Header(),
    )
    coordinator = LocationCoordinator(
        window=SimpleNamespace(ui=ui),
        gallery_vm=gallery,
        gallery_store=store,
        navigation=navigation,
        detail_vm=_DetailVm(presentation),
    )
    return coordinator, gallery, store, navigation, ui


def _presentation(**overrides):
    defaults = dict(
        path=Path("/library/album/photo.jpg"),
        asset_id="asset-1",
        is_video=False,
        info={"gps": {"lat": 48.85, "lon": 2.35}, "rel": "album/photo.jpg"},
        location="Paris",
        live_motion_rel=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def scheduled_timers(monkeypatch):
    """Capture ``QTimer.singleShot`` so the deferred steps can be run by hand."""

    timer = MagicMock()
    monkeypatch.setattr(module.QTimer, "singleShot", timer)
    return timer


def _run_deferred(timer) -> None:
    for call in timer.call_args_list:
        call.args[1]()


def test_toggle_detail_map_builds_a_focused_asset(monkeypatch) -> None:
    coordinator, gallery, _store, _navigation, ui = _make(presentation=_presentation())
    monkeypatch.setattr(coordinator, "_add_header_back_button", lambda: None)

    coordinator.toggle_detail_map()

    assert len(gallery.focused) == 1
    asset = gallery.focused[0]
    assert asset.library_relative == "album/photo.jpg"
    assert (asset.latitude, asset.longitude) == (48.85, 2.35)
    assert asset.location_name == "Paris"
    assert ui.map_view.focused_mode == [True]
    assert ui.sidebar.static_nodes == ["Location"]
    assert coordinator._return_from_map_path == Path("/library/album/photo.jpg")


def test_toggle_detail_map_ignores_photos_without_gps() -> None:
    coordinator, gallery, _store, _navigation, _ui = _make(
        presentation=_presentation(info={"rel": "album/photo.jpg"})
    )

    coordinator.toggle_detail_map()

    assert gallery.focused == []
    assert coordinator._return_from_map_path is None


def test_toggle_detail_map_ignores_a_missing_presentation() -> None:
    coordinator, gallery, _store, _navigation, _ui = _make(presentation=None)

    coordinator.toggle_detail_map()

    assert gallery.focused == []


def test_show_all_forgets_the_originating_photo() -> None:
    coordinator, gallery, _store, _navigation, ui = _make()
    coordinator._return_from_map_path = Path("D:/library/photo.jpg")

    coordinator.show_all()

    assert coordinator._return_from_map_path is None
    assert gallery.all_shown == 1
    assert ui.map_view.focused_mode == [False]


def test_back_without_origin_opens_all_photos() -> None:
    coordinator, _gallery, _store, navigation, _ui = _make()

    coordinator.back_clicked()

    assert navigation.all_photos == 1


def test_return_from_map_reopens_the_originating_row(scheduled_timers) -> None:
    path = Path("/library/album/photo.jpg")
    coordinator, gallery, _store, _navigation, _ui = _make(rows={path: 7})
    coordinator._return_from_map_path = path

    coordinator.return_from_map()
    assert coordinator._return_from_map_path is None

    assert scheduled_timers.call_count == 1, "the reopen is deferred"
    _run_deferred(scheduled_timers)

    assert gallery.opened_rows == [7]


def test_returning_retries_while_the_row_is_not_loaded_yet(scheduled_timers) -> None:
    path = Path("/library/album/photo.jpg")
    coordinator, gallery, _store, _navigation, _ui = _make(rows={})

    coordinator._try_return_to_photo(path, 0)

    assert gallery.opened_rows == []
    assert [call.args[0] for call in scheduled_timers.call_args_list] == [100]


def test_sidebar_location_click_returns_to_the_photo() -> None:
    path = Path("/library/album/photo.jpg")
    coordinator, _gallery, _store, navigation, _ui = _make(rows={path: 3})
    coordinator._return_from_map_path = path

    coordinator.sidebar_return("Location")

    assert navigation._pending_detail_return == path
    assert navigation.all_photos == 1
    assert coordinator._return_from_map_path is None


def test_sidebar_return_ignores_other_nodes() -> None:
    coordinator, _gallery, _store, navigation, _ui = _make()
    coordinator._return_from_map_path = Path("D:/library/photo.jpg")

    coordinator.sidebar_return("Albums")

    assert navigation.all_photos == 0
    assert coordinator._return_from_map_path is not None


def test_header_back_button_is_created_once(qapp) -> None:
    from PySide6.QtWidgets import QPushButton, QWidget

    header = QWidget()
    layout = header.layout()
    if layout is None:
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(header)

    coordinator, _gallery, _store, navigation, _ui = _make()
    coordinator._window = SimpleNamespace(
        ui=SimpleNamespace(main_header=header, map_view=_MapView(), sidebar=_Sidebar())
    )
    coordinator._return_from_map_path = Path("D:/library/photo.jpg")

    coordinator._add_header_back_button()
    button = coordinator._header_back_btn
    assert isinstance(button, QPushButton)
    assert button in header.findChildren(QPushButton)

    coordinator._add_header_back_button()
    assert coordinator._header_back_btn is button, "the button is reused, not duplicated"

    button.click()
    assert navigation.all_photos == 0, "with an origin it returns to the photo instead"
