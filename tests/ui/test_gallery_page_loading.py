"""The search loading view uses the light macOS-style presentation."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gallery_page import GalleryPageWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(scope="module")
def page(qapp: QApplication) -> GalleryPageWidget:
    widget = GalleryPageWidget()
    widget.show()
    qapp.processEvents()
    yield widget


def test_model_phase_uses_plain_copy_and_spinner(
    page: GalleryPageWidget, qapp: QApplication
) -> None:
    page.show_model_loading()
    qapp.processEvents()

    assert "350" not in page._status_label.text()
    assert "首次使用" not in page._sub_label.text()
    assert page._spinner.isHidden() is False
    assert page._spinner._timer.isActive()

    page.hide_loading()
    assert page._spinner._timer.isActive() is False


def test_indexing_phase_shows_the_thin_progress_bar(
    page: GalleryPageWidget, qapp: QApplication
) -> None:
    page.show_indexing_progress(30, 100)
    qapp.processEvents()

    assert page._progress_bar.isHidden() is False
    assert "30%" in page._detail_label.text()
