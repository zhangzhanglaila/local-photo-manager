"""Un-favouriting inside the Favorites collection removes the row.

Leaving the row in place kept the model reporting a row the query no longer returns,
which rendered as a black tile after navigating away and back.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _Service:
    def count_query_assets(self, query) -> int:  # pragma: no cover - not used
        return 3


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
        is_favorite=True,
        face_status=0,
        is_live=False,
        is_pano=False,
    )


def _store(tmp_path: Path) -> GalleryCollectionStore:
    root = tmp_path / "Library"
    root.mkdir()
    store = GalleryCollectionStore(_Service(), root)
    store._row_cache = {0: _dto("a"), 1: _dto("b"), 2: _dto("c")}
    store._total_count = 3
    store._row_mapping_key = store._count_cache_key()
    return store


def test_unfavouriting_removes_the_row_from_the_favourites_query(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._current_query = AssetQuery(is_favorite=True)

    store.update_favorite_status(1, False)

    assert store.count() == 2, "the row left the collection"
    assert store._row_cache[0].id == "as_a", "earlier rows stay put"
    assert store._row_cache[1].id == "as_c", "later rows shift up; nothing is left black"


def test_unfavouriting_outside_favourites_keeps_the_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._current_query = AssetQuery()

    store.update_favorite_status(1, False)

    assert store.count() == 3
    assert store._row_cache[1].is_favorite is False
