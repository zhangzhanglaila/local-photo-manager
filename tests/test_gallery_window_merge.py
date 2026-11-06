"""A newly loaded window merges into the cache instead of resetting the model.

Row numbers are absolute (``rowCount`` is the full query count), so a new window adds
data to rows that already exist. Replacing the cache and resetting the model threw away
the layout, the selection and the scroll offset — and cost hundreds of milliseconds on
the main thread, which is why the drag lagged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from datetime import datetime, timezone

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


def _dto(name: str) -> AssetDTO:
    return AssetDTO(
        id=f"as_{name}",
        abs_path=Path(f"/library/{name}.jpg"),
        rel_path=Path(f"{name}.jpg"),
        media_type=1,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        width=100,
        height=100,
        duration=0.0,
        size_bytes=1,
        metadata={},
        is_favorite=False,
        face_status=0,
        is_live=False,
        is_pano=False,
    )


class _Service:
    def count_query_assets(self, query) -> int:  # pragma: no cover - not used
        return 100


def _store(tmp_path: Path) -> GalleryCollectionStore:
    root = tmp_path / "Library"
    root.mkdir()
    store = GalleryCollectionStore(_Service(), root)
    store.set_active_root(root)
    store._current_query = AssetQuery()
    store._total_count = 0
    return store


def _finish(store: GalleryCollectionStore, rows, total: int) -> None:
    """Deliver a load result for the current generation (stale ones are discarded)."""

    store._load_generation += 1
    store._on_load_finished(store._load_generation, rows, total)


def test_second_window_merges_and_only_reports_the_new_range(tmp_path: Path) -> None:
    store = _store(tmp_path)
    loaded: list[tuple[int, int]] = []
    store.rows_loaded.connect(lambda first, last: loaded.append((first, last)))

    _finish(store, {0: _dto("a"), 1: _dto("b")}, 100)
    assert store._row_cache and min(store._row_cache) == 0

    _finish(store, {2: _dto("c"), 3: _dto("d")}, 100)

    assert set(store._row_cache) == {0, 1, 2, 3}, "the first window stays loaded"
    assert loaded == [(2, 3)]


def test_a_changed_query_still_resets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resets: list[int] = []
    store.data_changed.connect(lambda: resets.append(1))

    _finish(store, {0: _dto("a")}, 100)
    store._current_query = AssetQuery(limit=10)
    _finish(store, {0: _dto("a")}, 100)

    assert len(resets) == 2, "a new mapping must publish a full reset"


def test_a_changed_count_still_resets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resets: list[int] = []
    store.data_changed.connect(lambda: resets.append(1))

    _finish(store, {0: _dto("a")}, 100)
    _finish(store, {0: _dto("a")}, 120)

    assert len(resets) == 2, "rowCount changed, so the model must reset"
