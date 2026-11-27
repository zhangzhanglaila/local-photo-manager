"""Empty-state messaging while a scan is still writing the index.

Adding a new library root (or an album) makes it selectable before its assets
are indexed. The grid is empty at that moment; showing "No media found. Click
Rescan" made the folder look empty instead of busy.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.i18n import tr


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
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def test_rescan_shows_the_scanning_hint_instead_of_no_media(view: GalleryGridView, qapp: QApplication) -> None:
    view.set_scan_completed()
    qapp.processEvents()
    assert view._placeholder_label.isHidden() is False
    assert view._placeholder_label.text() == tr("empty.no_media")

    # A later scan (e.g. a second library root being indexed) must not claim the
    # folder is empty while rows are still being written.
    view.set_scan_started()
    qapp.processEvents()
    assert view._placeholder_label.isHidden() is False
    assert view._placeholder_label.text() == tr("empty.scanning")


def test_finished_scan_returns_to_the_empty_message(view: GalleryGridView, qapp: QApplication) -> None:
    view.set_scan_completed()
    view.set_scan_started()
    qapp.processEvents()

    view.set_scan_completed()
    qapp.processEvents()

    assert view._placeholder_label.isHidden() is False
    assert view._placeholder_label.text() == tr("empty.no_media")


def test_the_startup_phase_stays_blank(view: GalleryGridView, qapp: QApplication) -> None:
    # No scan has finished and nothing is loading: the window-level startup
    # overlay reports progress, so the grid must not paint its own message.
    assert view._placeholder_label.isHidden() is True
