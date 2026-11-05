"""Timeline rail: year bookmarks and the floating date badge.

The rail must derive everything from the model's cached year/row mapping. If it
queried the database per label it would undo the gallery's windowed loading, so
these tests pin the data contract rather than pixel output.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QAbstractListModel, QPoint, Qt
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


def test_scrubber_click_emits_the_year_row(qapp: QApplication) -> None:
    scrubber = TimelineScrubber()
    scrubber.resize(58, 400)
    scrubber.set_years([(2025, 0), (2024, 120), (2019, 900)])
    scrubber.show()
    qapp.processEvents()

    emitted: list[int] = []
    scrubber.yearActivated.connect(emitted.append)

    target = scrubber.year_at(200)
    assert target is not None
    year, row = target

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(10, 200),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    scrubber.mousePressEvent(press)

    assert emitted == [row]
    assert year in (2025, 2024, 2019)


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
    assert scrubber.geometry().right() <= view.viewport().width()
    view.close()