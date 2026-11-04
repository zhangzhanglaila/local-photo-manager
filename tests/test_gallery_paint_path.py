"""Painting must never query the database.

``data()`` runs for every visible row on every repaint. When an uncached row fell
back to a synchronous query, a large scroll jump — crossing several months, for
example — queried the database once per newly visible row while the view was
repainting, which showed up as stutter.
"""

from __future__ import annotations

from pathlib import Path

from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _CountingQueryService:
    """Query surface that records how often it is asked for rows."""

    def __init__(self, assets: list[Asset], library_root: Path) -> None:
        self.assets = assets
        self.library_root = library_root
        self.reads = 0

    def count_query_assets(self, query: AssetQuery) -> int:
        return len(self.assets)

    def read_query_asset_rows(self, root: Path, query: AssetQuery):
        self.reads += 1
        offset = query.offset
        limit = query.limit if query.limit is not None else len(self.assets)
        rows = []
        for asset in self.assets[offset : offset + limit]:
            rows.append(
                {
                    "id": asset.id,
                    "rel": asset.path.as_posix(),
                    "media_type": 0,
                    "bytes": asset.size_bytes,
                    "dt": None,
                    "w": None,
                    "h": None,
                    "dur": None,
                    "is_favorite": False,
                    "parent_album_path": None,
                }
            )
        return rows


def _store(tmp_path: Path) -> tuple[GalleryCollectionStore, _CountingQueryService]:
    library = tmp_path / "Library"
    library.mkdir()
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(400)
    ]
    service = _CountingQueryService(assets, library)
    store = GalleryCollectionStore(service, library_root=library)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store._active_root = library
    store._current_query = AssetQuery()
    store._total_count = len(assets)
    store._window_range = (0, 199)
    store._visible_range = (200, 260)
    store._row_cache = {row: object() for row in range(200)}  # type: ignore[assignment]
    return store, service


def test_peeking_an_uncached_row_does_not_query(tmp_path: Path) -> None:
    store, service = _store(tmp_path)
    before = service.reads

    assert store.peek_asset(250) is None
    assert service.reads == before, "the paint path must not query the database"


def test_requested_row_is_loaded_from_the_queue(tmp_path: Path) -> None:
    store, service = _store(tmp_path)
    changed: list[int] = []
    store.row_changed.connect(lambda row: changed.append(row))

    # A jump to a new range leaves the newly visible rows uncached.
    store._row_cache = {row: object() for row in range(200)}  # type: ignore[assignment]
    assert store.peek_asset(250) is None

    store.request_row(250)
    assert service.reads == 0, "queuing must not block the caller"

    store._preload_next()

    assert service.reads == 1, "the queued row should be read exactly once"
    assert store.peek_asset(250) is not None
    assert changed == [250], "the row must be repainted once its data arrives"


def test_requesting_a_cached_row_does_nothing(tmp_path: Path) -> None:
    store, service = _store(tmp_path)
    before = service.reads

    store.request_row(10)
    store._preload_next()

    assert service.reads == before
