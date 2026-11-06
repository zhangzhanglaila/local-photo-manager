"""The row count is cached per query scope.

Every window load used to re-run ``count_query_assets`` (29–75 ms measured) even though
the count only changes when the query changes or a scan writes rows. The cache is keyed
by the query object, so any filter/sort change misses it automatically instead of
depending on hand-written invalidation points.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _CountingService:
    def __init__(self) -> None:
        self.count_calls = 0

    def count_query_assets(self, query) -> int:
        self.count_calls += 1
        return 42

    def read_query_asset_rows(self, root, query):
        return []


def _store(tmp_path: Path) -> tuple[GalleryCollectionStore, _CountingService]:
    root = tmp_path / "Library"
    root.mkdir()
    service = _CountingService()
    store = GalleryCollectionStore(service, root)
    store.set_active_root(root)
    return store, service


def test_count_is_cached_for_the_same_query(tmp_path: Path) -> None:
    store, service = _store(tmp_path)
    store._current_query = AssetQuery()

    first = store._cached_total()
    assert first is None

    store._store_total(42)
    assert store._cached_total() == 42


def test_a_different_query_misses_the_cache(tmp_path: Path) -> None:
    store, _service = _store(tmp_path)
    store._current_query = AssetQuery()
    store._store_total(42)

    store._current_query = AssetQuery(limit=100)

    assert store._cached_total() is None


def test_cache_follows_the_active_root(tmp_path: Path) -> None:
    store, _service = _store(tmp_path)
    store._current_query = AssetQuery()
    store._store_total(42)

    other = tmp_path / "Other"
    other.mkdir()
    store.set_active_root(other)

    assert store._cached_total() is None
