"""Online raster tiles: stale-request cancellation and coalesced repaints.

Panning or zooming left the old tile requests queued on the host's limited
connections, so the tiles actually on screen waited behind stale ones and large
areas stayed blank. Every arriving tile also triggered a full repaint, which
turned a burst of arrivals into a burst of frames.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication

from maps.map_sources import MapSourceSpec
from maps.map_widget.leaflet_online_widget import (
    MAX_INFLIGHT_TILES,
    LeafletOnlineMapWidget,
)


class _FakeReply:
    def __init__(self) -> None:
        self.aborted = False
        self.deleted = False

    def abort(self) -> None:
        self.aborted = True

    def deleteLater(self) -> None:  # noqa: N802 - Qt-style name
        self.deleted = True


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def widget(qapp: QApplication) -> LeafletOnlineMapWidget:
    view = LeafletOnlineMapWidget(map_source=MapSourceSpec.gaode_standard())
    view.resize(640, 480)
    yield view
    view.deleteLater()


def test_stale_requests_are_aborted(widget: LeafletOnlineMapWidget) -> None:
    keep_key = (10, 100, 200)
    stale_key = (10, 101, 200)
    keep_reply, stale_reply = _FakeReply(), _FakeReply()
    widget._inflight[keep_key] = keep_reply  # type: ignore[assignment]
    widget._inflight[stale_key] = stale_reply  # type: ignore[assignment]
    widget._tiles[stale_key] = None

    widget._cancel_stale_requests({keep_key})

    assert stale_reply.aborted is True
    assert stale_reply.deleted is True
    assert stale_key not in widget._inflight
    assert stale_key not in widget._tiles, "the placeholder is dropped so it can be re-requested"
    assert keep_key in widget._inflight
    assert keep_reply.aborted is False


def test_tile_arrivals_coalesce_into_one_repaint(widget: LeafletOnlineMapWidget) -> None:
    assert widget._repaint_timer.isActive() is False

    for _ in range(5):
        widget._schedule_repaint()

    assert widget._repaint_timer.isActive() is True
    assert widget._repaint_timer.interval() <= 33, "at most one frame of latency"


def test_inflight_requests_are_capped(widget: LeafletOnlineMapWidget) -> None:
    for index in range(MAX_INFLIGHT_TILES):
        widget._inflight[(10, index, 0)] = _FakeReply()  # type: ignore[assignment]

    # A new key cannot be requested while the cap is reached; the next paint
    # retries it instead of queueing behind everything else.
    widget._request_tile((10, 999, 0))

    assert (10, 999, 0) not in widget._inflight
    assert (10, 999, 0) not in widget._tiles
