"""Opening a library must put photos in the grid.

This is the regression that made "All Photos" look broken twice: rows existed in
the index and the count was right, but the first window came back empty, so the
grid stayed blank while the status bar claimed thousands of photos. The store is
driven here exactly like the gallery view model drives it — real index rows, real
query surface, the real background load worker — with no window involved.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="the load worker is a Qt runnable")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iPhoto.application.dtos import AssetDTO  # noqa: E402
from iPhoto.cache.index_store import get_global_repository, reset_global_repository  # noqa: E402
from iPhoto.domain.models.query import AssetQuery  # noqa: E402
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore  # noqa: E402
from iPhoto.infrastructure.services.library_asset_query_service import (  # noqa: E402
    LibraryAssetQueryService,
)


@pytest.fixture(autouse=True)
def _reset_global_index():
    reset_global_repository()
    yield
    reset_global_repository()


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _make_library(root: Path) -> list[dict]:
    rows = []
    for album, count in (("202401_a", 2), ("202405_b", 3)):
        album_root = root / album
        album_root.mkdir(parents=True)
        for index in range(count):
            path = album_root / f"IMG_{index}.jpg"
            path.write_bytes(b"jpeg-bytes")
            rows.append(
                {
                    "rel": f"{album}/IMG_{index}.jpg",
                    "id": f"asset-{album}-{index}",
                    "dt": "2024-01-05T10:00:00Z" if album == "202401_a" else "2024-05-05T10:00:00Z",
                    "ts": 1704448800 if album == "202401_a" else 1714903200,
                    "bytes": path.stat().st_size,
                    "mime": "image/jpeg",
                    "year": 2024,
                    "month": 1 if album == "202401_a" else 5,
                    "media_type": 1,
                }
            )
    return rows


def _wait_for_rows(store: GalleryCollectionStore, qapp, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if store.peek_asset(0) is not None:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"gallery window stayed empty: count={store.count()} rows={len(store._row_cache)}"
    )


def test_first_window_loads_real_rows(tmp_path: Path, qapp) -> None:
    library_root = tmp_path / "Library"
    rows = _make_library(library_root)
    get_global_repository(library_root).write_rows(rows)

    service = LibraryAssetQueryService(library_root)
    store = GalleryCollectionStore(service, library_root)

    store.load_selection(active_root=library_root, query=AssetQuery())
    _wait_for_rows(store, qapp)

    assert store.count() == len(rows)
    first = store.peek_asset(0)
    assert isinstance(first, AssetDTO)
    assert first.abs_path.exists(), first.abs_path
    assert len(store._row_cache) >= len(rows)


def test_timeline_years_match_the_rows_they_point_at(tmp_path: Path, qapp) -> None:
    library_root = tmp_path / "Library"
    rows = _make_library(library_root)
    get_global_repository(library_root).write_rows(rows)

    service = LibraryAssetQueryService(library_root)
    store = GalleryCollectionStore(service, library_root)
    store.load_selection(active_root=library_root, query=AssetQuery())
    _wait_for_rows(store, qapp)

    spans = store.year_spans()
    assert [year for year, _row, _count in spans] == [2024]
    year, first_row, count = spans[0]
    assert (first_row, count) == (0, len(rows))

    month = store.month_for_row(0)
    assert month is not None and month[0] == 2024
