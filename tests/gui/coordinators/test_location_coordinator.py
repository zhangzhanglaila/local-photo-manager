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
from iPhoto.i18n import tr  # noqa: E402


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
        self.trail = None
        self.trail_cleared = 0
        self.btn_trail_toggle = SimpleNamespace(checked=[], setChecked=self._trail_checked)
        self.btn_person_filter = SimpleNamespace(checked=[], setChecked=self._person_checked)
        self.person_filter = None
        self.person_filter_hidden = 0

    def _trail_checked(self, value: bool) -> None:
        self.btn_trail_toggle.checked.append(value)

    def _person_checked(self, value: bool) -> None:
        self.btn_person_filter.checked.append(value)

    def set_trail(self, trail) -> None:
        self.trail = trail

    def clear_trail(self) -> None:
        self.trail_cleared += 1

    def show_person_filter(self, service) -> None:
        self.person_filter = service

    def hide_person_filter(self) -> None:
        self.person_filter_hidden += 1

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


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int | None]] = []

    def show_message(self, message, timeout_ms=None) -> None:
        self.messages.append((message, timeout_ms))


class _Library:
    def __init__(self, assets) -> None:
        self._assets = assets

    def get_geotagged_assets(self):
        return self._assets


def _make_map(*, assets=None, status_bar=None, people_service=None):
    gallery = _GalleryVm()
    ui = SimpleNamespace(
        map_view=_MapView(), sidebar=_Sidebar(), main_header=_Header()
    )
    coordinator = LocationCoordinator(
        window=SimpleNamespace(ui=ui),
        gallery_vm=gallery,
        gallery_store=_Store(),
        navigation=_Navigation(),
        detail_vm=_DetailVm(None),
        library_getter=lambda: (None if assets is None else _Library(assets)),
        status_bar=status_bar,
        people_service_getter=lambda: people_service,
    )
    return coordinator, ui


def test_hiding_the_trail_clears_it() -> None:
    status = _StatusBar()
    coordinator, ui = _make_map(assets=[], status_bar=status)

    coordinator.toggle_trail(False)

    assert ui.map_view.trail_cleared == 1
    assert status.messages == [(tr("map.trail.hidden"), 2000)]


def test_trail_without_a_library_unchecks_the_button() -> None:
    status = _StatusBar()
    coordinator, ui = _make_map(assets=None, status_bar=status)

    coordinator.toggle_trail(True)

    assert ui.map_view.btn_trail_toggle.checked == [False]
    assert status.messages == [(tr("map.trail.unavailable"), 4000)]


def test_trail_without_geotagged_photos_explains_why() -> None:
    status = _StatusBar()
    coordinator, ui = _make_map(assets=[], status_bar=status)

    coordinator.toggle_trail(True)

    assert ui.map_view.btn_trail_toggle.checked == [False]
    assert status.messages == [(tr("map.trail.no_gps"), 5000)]


def test_trail_build_failure_is_reported(monkeypatch) -> None:
    status = _StatusBar()
    coordinator, ui = _make_map(assets=[object()], status_bar=status)

    class _BrokenTrailService:
        def build_trail(self, *_args, **_kwargs):
            raise RuntimeError("no timestamps")

    import iPhoto.application.services.trail_service as trail_module

    monkeypatch.setattr(trail_module, "TrailService", _BrokenTrailService)

    coordinator.toggle_trail(True)

    assert ui.map_view.btn_trail_toggle.checked == [False]
    message, timeout = status.messages[-1]
    assert message == tr("map.trail.failed", message="no timestamps")
    assert timeout == 5000


def test_trail_success_reports_the_counts(monkeypatch) -> None:
    status = _StatusBar()
    coordinator, ui = _make_map(assets=[object(), object()], status_bar=status)

    class _TrailService:
        def build_trail(self, _assets, *, granularity):
            assert granularity == "day"
            return SimpleNamespace(is_empty=False, total_photos=2, segments=[1, 2, 3])

    import iPhoto.application.services.trail_service as trail_module

    monkeypatch.setattr(trail_module, "TrailService", _TrailService)

    coordinator.toggle_trail(True)

    assert ui.map_view.trail is not None
    assert status.messages[-1] == (
        tr("map.trail.summary", photos=2, segments=3),
        4000,
    )


def test_person_filter_uses_the_bound_service() -> None:
    service = object()
    coordinator, ui = _make_map(people_service=service)

    coordinator.toggle_person_filter(True)
    coordinator.toggle_person_filter(False)

    assert ui.map_view.person_filter is service
    assert ui.map_view.person_filter_hidden == 1
