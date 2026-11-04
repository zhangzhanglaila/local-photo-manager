"""The asset grid must report which rows are on screen.

``visibleRowsChanged`` is what lets the gallery store keep a cache window around
the viewport. If the grid never reports its visible rows, the store cannot
prefetch, and every newly revealed row is fetched one at a time while the user
scrolls — which is exactly what makes scrolling feel sluggish.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for grid tests",
    exc_type=ImportError,
)

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt
from PySide6.QtWidgets import QApplication, QListView

from iPhoto.gui.ui.widgets.asset_grid import AssetGrid


class _RowModel(QAbstractListModel):
    def __init__(self, count: int) -> None:
        super().__init__()
        self._count = count

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._count

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return f"row-{index.row()}"
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(120, 120)
        return None


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _pump(qapp: QApplication, seconds: float = 0.4) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


def _wait_until(qapp: QApplication, predicate, timeout: float = 8.0) -> bool:
    """Process events until *predicate* holds or the timeout expires.

    A fixed sleep made this test flaky inside the full suite: the grid reports
    its visible rows from a timer, and under load that timer can fire later than
    a hard-coded wait allows.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_asset_grid_reports_visible_rows_and_updates_on_scroll(qapp: QApplication) -> None:
    grid = AssetGrid()
    grid.setViewMode(QListView.ViewMode.IconMode)
    grid.setResizeMode(QListView.ResizeMode.Adjust)
    grid.setGridSize(QSize(130, 130))
    grid.setModel(_RowModel(500))

    reported: list[tuple[int, int]] = []
    grid.visibleRowsChanged.connect(lambda first, last: reported.append((first, last)))

    grid.resize(800, 500)
    grid.show()
    _wait_until(qapp, lambda: bool(reported))

    assert reported, "the grid must report its visible rows after it is shown"
    first_initial = reported[-1]
    assert 0 <= first_initial[0] <= first_initial[1] < 500

    reported.clear()
    scrollbar = grid.verticalScrollBar()
    assert scrollbar.maximum() > 0, "the model must be tall enough to scroll"
    scrollbar.setValue(scrollbar.maximum())
    _wait_until(qapp, lambda: bool(reported))

    assert reported, "scrolling must report a new visible range"
    first_scrolled = reported[-1]
    assert first_scrolled[0] > first_initial[0], (
        f"scrolling to the end must move the visible range forward, "
        f"got {first_initial} then {first_scrolled}"
    )

    grid.close()
