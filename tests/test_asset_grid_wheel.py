"""One wheel notch must not jump several rows in the asset grid.

The grid scrolls per pixel, so the platform default of three lines per notch
moved a dense grid by several rows at once, which felt like overshooting.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListView

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


def _wheel(view, *, notches: int = 1) -> None:
    delta = -120 * notches
    position = QPointF(view.viewport().width() / 2, view.viewport().height() / 2)
    event = QWheelEvent(
        position,
        view.mapToGlobal(QPoint(int(position.x()), int(position.y()))).toPointF(),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(view.viewport(), event)


def _configure(view: QListView, model: _RowModel) -> QListView:
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setGridSize(QSize(130, 130))
    view.setModel(model)
    view.resize(800, 500)
    view.show()
    QApplication.processEvents()
    return view


def test_one_notch_scrolls_less_than_the_platform_default(qapp: QApplication) -> None:
    model = _RowModel(500)
    default_view = _configure(QListView(), model)
    grid = _configure(AssetGrid(), _RowModel(500))

    default_bar = default_view.verticalScrollBar()
    grid_bar = grid.verticalScrollBar()
    assert default_bar.maximum() > 0 and grid_bar.maximum() > 0

    _wheel(default_view)
    _wheel(grid)

    default_travel = default_bar.value()
    grid_travel = grid_bar.value()
    assert default_travel > 0, "the reference view should scroll"
    assert 0 < grid_travel < default_travel, (
        f"one notch should travel less than the default "
        f"(default={default_travel}, grid={grid_travel})"
    )

    default_view.close()
    grid.close()


def test_wheel_up_still_scrolls_back(qapp: QApplication) -> None:
    grid = _configure(AssetGrid(), _RowModel(500))
    bar = grid.verticalScrollBar()

    _wheel(grid, notches=4)
    assert bar.value() > 0

    _wheel(grid, notches=-4)
    assert bar.value() == 0, "scrolling up must return to the top"

    grid.close()
