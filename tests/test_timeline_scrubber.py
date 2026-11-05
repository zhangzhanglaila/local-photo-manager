"""Timeline rail: year bookmarks and the floating date badge.

The rail must derive everything from the model's cached year/row mapping. If it
queried the database per label it would undo the gallery's windowed loading, so
these tests pin the data contract rather than pixel output.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QAbstractListModel, QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.widgets.timeline_scrubber import TimelineScrubber
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeQuerySurface:
    library_root = None

    def count_query_assets(self, query) -> int:
        return 10

    def read_query_asset_rows(self, root, query):
        return []

    def count_query_buckets(self, query):
        return [(2025, 7, 4), (2025, 6, 2), (2024, 12, 3), (2019, 5, 1)]


def _store() -> GalleryCollectionStore:
    from iPhoto.domain.models.query import AssetQuery

    store = GalleryCollectionStore(_FakeQuerySurface())
    store._current_query = AssetQuery()
    store._total_count = 10
    return store


def test_year_start_rows_follow_cumulative_month_counts() -> None:
    store = _store()

    assert store.timeline_buckets() == [
        (2025, 7, 4),
        (2025, 6, 2),
        (2024, 12, 3),
        (2019, 5, 1),
    ]
    # 2025 covers 6 rows, so 2024 starts at 6 and 2019 at 9.
    assert store.year_start_rows() == [(2025, 0), (2024, 6), (2019, 9)]


def test_year_start_rows_are_clamped_to_the_row_count() -> None:
    store = _store()
    store._total_count = 1

    rows = [row for _year, row in store.year_start_rows()]

    assert rows == sorted(rows)
    assert all(0 <= row <= 0 for row in rows)


def test_buckets_are_cached_per_query() -> None:
    calls: list[int] = []

    class _Counting(_FakeQuerySurface):
        def count_query_buckets(self, query):
            calls.append(1)
            return super().count_query_buckets(query)

    from iPhoto.domain.models.query import AssetQuery

    store = GalleryCollectionStore(_Counting())
    store._current_query = AssetQuery()
    store._total_count = 10

    store.timeline_buckets()
    store.timeline_buckets()

    assert len(calls) == 1, "the rail must not re-query on every repaint"


def test_scrubber_hides_for_a_single_year(qapp: QApplication) -> None:
    scrubber = TimelineScrubber()
    scrubber.resize(58, 400)

    scrubber.set_years([(2025, 0)])
    assert scrubber.isVisible() is False

    scrubber.set_years([(2025, 0), (2024, 120)])
    qapp.processEvents()
    assert scrubber.isVisible() is True


def test_scrubber_press_emits_a_scrub_position(qapp: QApplication) -> None:
    scrubber = TimelineScrubber()
    scrubber.resize(58, 400)
    scrubber.set_years([(2025, 0), (2024, 120), (2019, 900)])
    scrubber.show()
    qapp.processEvents()

    emitted: list[tuple[int, int]] = []
    scrubber.scrubMoved.connect(lambda row, y: emitted.append((row, y)))

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(10, 200),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    scrubber.mousePressEvent(press)

    assert len(emitted) == 1
    row, y = emitted[0]
    assert y == 200
    assert 0 <= row < scrubber.total_rows()


def test_scrubber_paints_without_a_model(qapp: QApplication) -> None:
    scrubber = TimelineScrubber()
    scrubber.resize(58, 400)
    scrubber.set_years([(2025, 0), (2024, 120)])
    scrubber.show()
    qapp.processEvents()

    pixmap = scrubber.grab()
    assert not pixmap.isNull()

class _GridModel(QAbstractListModel):
    """Minimal model exposing the timeline contract the grid consumes."""

    def rowCount(self, parent=None) -> int:  # type: ignore[override]
        return 500

    def data(self, index, role: int = 0):
        if int(role) == int(Roles.DT):
            return datetime(2024, 5, 1)
        return None

    def year_start_rows(self):
        return [(2025, 0), (2024, 120), (2019, 400)]

    def year_spans(self):
        return [(2025, 0, 300), (2024, 120, 280), (2019, 400, 20)]


def test_grid_view_shows_the_year_rail(qapp: QApplication) -> None:
    from PySide6.QtCore import QAbstractListModel

    from iPhoto.gui.ui.models.roles import Roles
    from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView

    view = GalleryGridView()
    view.resize(900, 600)
    view.setModel(_GridModel())
    view.show()
    qapp.processEvents()
    view._refresh_timeline()
    qapp.processEvents()

    scrubber = view._timeline_scrubber
    assert scrubber.years() == [(2025, 0), (2024, 120), (2019, 400)]
    assert scrubber.isVisible() is True
    # The rail lives outside the viewport in its own reserved channel.\r\n    assert scrubber.geometry().left() >= view.viewport().width()
    view.close()

def test_rail_sizes_years_by_photo_count(qapp: QApplication) -> None:
    """A year holding thousands of photos must occupy more of the track."""

    scrubber = TimelineScrubber()
    scrubber.resize(52, 600)
    scrubber.set_spans(
        [
            (2025, 0, 5000),
            (2024, 5000, 400),
            (2023, 5400, 3),
        ]
    )
    scrubber.show()
    qapp.processEvents()

    ys = scrubber._layout_y
    big_span = ys[1] - ys[0]
    small_span = ys[2] - ys[1]

    assert big_span > 200, "the dominant year should own most of the rail"
    assert big_span > small_span * 5


def test_dense_years_keep_a_readable_gap(qapp: QApplication) -> None:
    """Tiny years are nudged apart instead of stacking on one pixel."""

    scrubber = TimelineScrubber()
    scrubber.resize(52, 400)
    scrubber.set_spans(
        [(2025, 0, 1000)] + [(year, 1000 + i, 2) for i, year in enumerate(range(2020, 2000, -1))]
    )
    scrubber.show()
    qapp.processEvents()

    ys = scrubber._layout_y
    gaps = [b - a for a, b in zip(ys, ys[1:])]

    assert min(gaps) >= 16.0
    assert ys == sorted(ys)
    assert ys[0] >= 0 and ys[-1] <= scrubber.height()


def test_rail_keeps_a_gap_from_the_photos(qapp: QApplication) -> None:
    from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView

    view = GalleryGridView()
    view.resize(1000, 640)
    view.setModel(_GridModel())
    view.show()
    qapp.processEvents()
    view._refresh_timeline()
    qapp.processEvents()

    scrubber = view._timeline_scrubber
    assert scrubber.isVisible()
    # Reserved channel means the rail starts at (or after) the viewport edge.
    assert scrubber.geometry().left() >= view.viewport().width()
    assert view.viewport().width() <= view.width() - scrubber.width() - view.TIMELINE_GAP + 2
    view.close()

def test_row_at_is_monotonic_and_clamped(qapp: QApplication) -> None:
    scrubber = TimelineScrubber()
    scrubber.resize(52, 600)
    scrubber.set_spans([(2025, 0, 5000), (2024, 5000, 400), (2019, 5400, 20)])
    scrubber.show()
    qapp.processEvents()

    rows = [scrubber.row_at(y) for y in range(0, 600, 10)]

    assert all(row is not None for row in rows)
    assert rows == sorted(rows)
    assert rows[0] >= 0
    assert rows[-1] <= scrubber.total_rows() - 1


def test_hover_emits_row_and_leave_clears_it(qapp: QApplication) -> None:
    scrubber = TimelineScrubber()
    scrubber.resize(52, 600)
    scrubber.set_spans([(2025, 0, 5000), (2024, 5000, 400), (2019, 5400, 20)])
    scrubber.show()
    qapp.processEvents()

    seen: list[tuple[int, int]] = []
    scrubber.hoverRowChanged.connect(lambda row, y: seen.append((row, y)))

    move = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPoint(20, 300),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    scrubber.mouseMoveEvent(move)

    assert seen and seen[-1][1] == 300
    assert seen[-1][0] >= 0

    scrubber.leaveEvent(QEvent(QEvent.Type.Leave))
    assert seen[-1][0] == -1

def test_hover_shows_the_date_bubble_beside_the_rail(qapp: QApplication) -> None:
    from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView

    view = GalleryGridView()
    view.resize(1000, 640)
    view.setModel(_GridModel())
    view.show()
    qapp.processEvents()
    view._refresh_timeline()
    qapp.processEvents()

    view._on_timeline_hover(120, 300)
    qapp.processEvents()

    badge = view._date_badge
    assert badge.isVisible() is True
    assert "2024" in badge.text()

    rail_left = view._timeline_scrubber.geometry().left()
    # Like the reference, the pill overlaps the track column instead of stopping
    # short of it, but it must stay inside the window.
    assert badge.geometry().right() >= rail_left
    assert badge.geometry().right() <= view.width()

    view._on_timeline_hover(-1, -1)
    qapp.processEvents()
    assert badge.isVisible() is False
    view.close()


def test_scrub_moves_the_grid_and_throttles_scroll(qapp: QApplication) -> None:
    from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView

    view = GalleryGridView()
    view.resize(1000, 640)
    view.setModel(_GridModel())
    view.show()
    qapp.processEvents()
    view._refresh_timeline()
    qapp.processEvents()

    view._on_timeline_scrub(200, 400)
    assert view._scrub_pending_row == 200
    # The timer coalesces bursts into one scroll instead of fighting the layout.
    assert view._scrub_timer.isActive()
    view.close()