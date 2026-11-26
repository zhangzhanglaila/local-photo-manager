"""Static sidebar nodes dispatch through a table keyed by node id.

``_handle_static_node`` used to be a seven-branch ladder comparing English titles
plus one branch that compared ``name == tr("sidebar.albums")`` — the Albums header
emitted its *translated* label, so routing only worked in one language and the
translated comparison had to be carried along forever.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore", reason="the coordinator is a Qt object")

from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator  # noqa: E402


class _GalleryViewModel:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def open_albums_dashboard(self) -> None:
        self.calls.append(("dashboard",))

    def open_filtered_collection(self, title, **kwargs) -> None:
        self.calls.append(("filtered", title, kwargs))


class _Host(NavigationCoordinator):
    """NavigationCoordinator without a window: only the dispatch surface is used."""

    _logger = logging.getLogger("test.host")

    def __init__(self) -> None:  # noqa: D107 - deliberately skips QObject.__init__
        self._gallery_vm = _GalleryViewModel()
        self._pending_detail_return: Path | None = None
        self.empty_modes: list[str | None] = []
        self.opened: list[str] = []
        self.began = 0

    def _begin_navigation(self) -> None:
        self.began += 1

    def _set_grid_empty_mode(self, mode) -> None:
        self.empty_modes.append(mode)

    def open_all_photos(self) -> None:
        self.opened.append("all_photos")

    def open_recently_deleted(self) -> None:
        self.opened.append("recently_deleted")

    def open_people_view(self) -> None:
        self.opened.append("people")

    def open_location_view(self) -> None:
        self.opened.append("location")


def test_every_route_resolves_to_a_handler() -> None:
    for node_id, route in NavigationCoordinator._STATIC_NODE_ROUTES.items():
        assert callable(
            getattr(NavigationCoordinator, f"_open_static_{route}", None)
        ), node_id


def test_dispatch_uses_the_node_id_not_the_label() -> None:
    host = _Host()

    NavigationCoordinator._handle_static_node(host, "Favorites")

    assert [call[0] for call in host._gallery_vm.calls] == ["filtered"]
    assert host.empty_modes == ["favorites"]

    # A translated label must not route: identity is the id, not the caption.
    NavigationCoordinator._handle_static_node(host, "相册")
    assert len(host._gallery_vm.calls) == 1

    NavigationCoordinator._handle_static_node(host, "Albums")
    assert host._gallery_vm.calls[-1] == ("dashboard",)


def test_unknown_node_is_ignored() -> None:
    host = _Host()

    NavigationCoordinator._handle_static_node(host, "definitely not a node")

    assert host._gallery_vm.calls == []
    assert host.opened == []


def test_location_click_consumes_a_pending_detail_return() -> None:
    host = _Host()
    host._pending_detail_return = Path("photo.jpg")

    NavigationCoordinator._handle_static_node(host, "Location")

    assert host._pending_detail_return is None
    assert host.opened == []


def test_videos_and_live_photos_request_media_filters() -> None:
    host = _Host()

    NavigationCoordinator._handle_static_node(host, "Videos")
    NavigationCoordinator._handle_static_node(host, "Live Photos")

    stages = [call[0] for call in host._gallery_vm.calls]
    assert stages == ["filtered", "filtered"]
    assert host.empty_modes == ["videos", "live"]
    assert host.began == 2
