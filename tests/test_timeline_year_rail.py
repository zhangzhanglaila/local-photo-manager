"""The year rail must list each year exactly once, derived from the current view."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication

from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.ui.widgets.timeline_scrubber import TimelineScrubber
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _BucketService:
    def __init__(self, buckets):
        self.buckets = list(buckets)

    def count_query_assets(self, query) -> int:
        return sum(count for _y, _m, count in self.buckets)

    def read_query_asset_rows(self, root, query):
        return []

    def count_query_buckets(self, query):
        return list(self.buckets)


def _store(tmp_path: Path, buckets) -> GalleryCollectionStore:
    root = tmp_path / "Library"
    root.mkdir()
    store = GalleryCollectionStore(_BucketService(buckets), root)
    store.set_active_root(root)
    store.load_selection(root, query=AssetQuery())
    # The window loader runs on a worker in the app; the rail only needs the
    # total, so set it directly instead of waiting for the thread.
    store._total_count = sum(count for _y, _m, count in buckets)
    store._timeline_cache_key = None
    return store


def test_interleaved_months_do_not_repeat_a_year(tmp_path):
    # A non-contiguous same-year bucket (mixed order / scan inserting an old row)
    # used to produce two spans for 2025 and the rail drew "2025" twice.
    store = _store(
        tmp_path,
        [
            (2025, 10, 10),
            (2024, 7, 5),
            (2025, 7, 7),
            (2023, 1, 1),
        ],
    )

    spans = store.year_spans()
    years = [year for year, _row, _count in spans]

    assert years == [2025, 2024, 2023]
    assert spans[0][2] == 17, "the same year's months must be summed"


def test_row_offsets_follow_the_aggregated_years(tmp_path):
    store = _store(
        tmp_path,
        [
            (2025, 10, 10),
            (2024, 7, 5),
            (2025, 7, 7),
        ],
    )

    spans = store.year_spans()

    assert spans == [(2025, 0, 17), (2024, 17, 5)]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_scrubber_collapses_duplicate_years(qapp: QApplication):
    scrubber = TimelineScrubber()
    scrubber.resize(40, 600)

    scrubber.set_spans([(2025, 0, 10), (2025, 10, 5), (2024, 15, 2)])

    assert [year for year, _row, _count in scrubber.spans()] == [2025, 2024]
    assert scrubber.spans()[0][2] == 15
    scrubber.close()


def test_rail_does_not_hijack_the_cursor(qapp: QApplication):
    """Hovering the window must not turn the pointer into a hand."""

    from PySide6.QtCore import Qt

    scrubber = TimelineScrubber()
    try:
        assert scrubber.cursor().shape() == Qt.CursorShape.ArrowCursor
    finally:
        scrubber.close()
