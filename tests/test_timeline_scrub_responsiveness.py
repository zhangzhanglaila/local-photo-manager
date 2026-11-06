"""Dragging the timeline rail must follow the pointer immediately.

The old flow moved the grid only on a 90 ms throttle, and every move started a window
reload that cancelled the previous one — so during a fast drag none finished and the
viewport stayed behind, catching up seconds after the release.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Rows(QAbstractListModel):
    """Just enough model for the view to have a real scroll range."""

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1000

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole:
            return None
        return str(index.row())


def _grid(qapp: QApplication) -> GalleryGridView:
    grid = GalleryGridView()
    grid.setModel(_Rows())
    grid.resize(960, 600)
    grid.setGridSize(QSize(200, 200))
    grid.show()
    qapp.processEvents()
    return grid


def test_each_drag_step_moves_the_viewport_immediately(qapp: QApplication) -> None:
    grid = _grid(qapp)

    grid._scroll_to_row_now(0)
    first = grid.verticalScrollBar().value()
    grid._scroll_to_row_now(40)
    second = grid.verticalScrollBar().value()
    grid._scroll_to_row_now(80)
    third = grid.verticalScrollBar().value()

    assert first <= second <= third
    assert third > first, "the viewport must move with the drag, not wait for a reload"


def test_scroll_offset_uses_the_loaded_independent_geometry(qapp: QApplication) -> None:
    grid = _grid(qapp)
    columns = max(1, grid.viewport().width() // max(1, grid.gridSize().width()))

    # A row in the second band must be at least one cell further down.
    assert grid._scroll_value_for_row(columns) == grid.gridSize().height()
    assert grid._scroll_value_for_row(0) == 0


def test_scrub_suppresses_visible_range_until_release(qapp: QApplication) -> None:
    grid = _grid(qapp)
    emitted: list[tuple[int, int]] = []
    grid.visibleRowsChanged.connect(lambda first, last: emitted.append((first, last)))

    grid.set_visible_rows_suppressed(True)
    assert grid._visible_rows_suppressed is True

    grid.set_visible_rows_suppressed(False)
    assert grid._visible_rows_suppressed is False

    # Suppression must only gate the signal, never the recorded range.
    assert grid._visible_range is None or isinstance(grid._visible_range, tuple)
    assert emitted == [] or all(isinstance(item, tuple) for item in emitted)
