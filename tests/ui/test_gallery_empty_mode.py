"""Switching to 视频 / 实况 must not flash "没有视频" before the rows arrive.

The empty-state label was painted the moment the filter context was set, so a
video or Live Photo collection showed its "empty" message for a frame or two and
then replaced it with photos.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.i18n import tr


class _Rows(QAbstractListModel):
    """Minimal model so the view can report 0 / N rows."""

    def __init__(self) -> None:
        super().__init__()
        self._rows = 0

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else self._rows

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        return "row" if role == Qt.ItemDataRole.DisplayRole else None

    def set_rows(self, count: int) -> None:
        self.beginResetModel()
        self._rows = count
        self.endResetModel()


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def view(qapp: QApplication) -> GalleryGridView:
    widget = GalleryGridView()
    widget.resize(800, 600)
    widget.setModel(_Rows())
    widget.show()
    widget.set_scan_completed()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _placeholder_visible(view: GalleryGridView) -> bool:
    return not view._placeholder_label.isHidden()


def test_switching_to_videos_does_not_paint_the_empty_message(view: GalleryGridView, qapp):
    view.set_empty_mode("videos")
    qapp.processEvents()

    assert view._placeholder_label.text() == tr("empty.videos")
    # The query has not produced a result yet, so nothing may be painted.
    assert _placeholder_visible(view) is False


def test_the_message_appears_once_the_result_is_known_to_be_empty(view: GalleryGridView, qapp):
    view.set_empty_mode("live")
    qapp.processEvents()
    assert _placeholder_visible(view) is False

    # Simulate the result arriving: no rows appeared, so the safety timeout
    # resolves the pending query to "genuinely nothing".
    view._on_loading_timeout()
    qapp.processEvents()

    assert _placeholder_visible(view) is True
    assert view._placeholder_label.text() == tr("empty.live")


def test_rows_arriving_cancel_the_pending_message(view: GalleryGridView, qapp):
    model = view.model()
    view.set_empty_mode("videos")
    qapp.processEvents()

    model.set_rows(3)
    qapp.processEvents()

    assert _placeholder_visible(view) is False
    assert view._empty.mode is None


def test_a_model_reset_with_rows_clears_every_placeholder(view: GalleryGridView, qapp):
    view.set_empty_mode("videos")
    qapp.processEvents()

    view.model().set_rows(4)
    qapp.processEvents()

    assert _placeholder_visible(view) is False
    assert view._empty.mode is None


def test_clearing_the_mode_returns_to_the_generic_text(view: GalleryGridView, qapp):
    view.set_empty_mode("videos")
    qapp.processEvents()
    view.set_empty_mode(None)
    qapp.processEvents()

    assert view._placeholder_label.text() == tr("empty.no_media")
