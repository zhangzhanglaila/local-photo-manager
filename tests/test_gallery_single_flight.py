"""Fast scrolling must not cancel every load before it finishes.

``_reload_window_for_visible_range`` used to cancel the running load on every range
change. With a ~50-100 ms load and a wheel that changes the range every 30-50 ms,
nothing ever finished: the 2-thread pool filled with cancelled-but-still-running
workers and the position only landed after the user stopped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _Service:
    def count_query_assets(self, query) -> int:
        return 100

    def read_query_asset_rows(self, root, query):
        return []


def _store(tmp_path: Path) -> GalleryCollectionStore:
    root = tmp_path / "Library"
    root.mkdir()
    store = GalleryCollectionStore(_Service(), root)
    store.set_active_root(root)
    store._current_query = AssetQuery()
    store._total_count = 100
    return store


def test_a_new_range_while_loading_is_queued_not_cancelled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sentinel = object()
    store._load_worker = sentinel  # a load is "running"

    store._reload_window_for_visible_range(40, 80, emit_signals=True)

    assert store._load_worker is sentinel, "the running load must not be cancelled"
    assert store._queued_visible == (40, 80)


def test_only_the_newest_queued_range_survives(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._load_worker = object()

    store._reload_window_for_visible_range(10, 20, emit_signals=True)
    store._reload_window_for_visible_range(30, 40, emit_signals=True)
    store._reload_window_for_visible_range(50, 60, emit_signals=True)

    assert store._queued_visible == (50, 60), "intermediate positions are dropped"


def test_finishing_a_load_starts_the_queued_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._load_worker = object()
    store._reload_window_for_visible_range(70, 90, emit_signals=True)

    started: list[tuple[int, int]] = []
    store._load_worker = None
    store._start_queued_load = lambda: started.append(store._queued_visible)  # type: ignore[assignment]

    # The queued range is consumed when the next load is started.
    queued = store._queued_visible
    store._queued_visible = None
    assert queued == (70, 90)
