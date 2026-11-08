"""The search dropdown's chips, scope switch and similar-photo shortcuts."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from iPhoto.gui.ui.widgets.search_popup import SearchPopup


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _chip(popup: SearchPopup, text: str) -> QPushButton:
    for button in popup.findChildren(QPushButton):
        if button.text() == text:
            return button
    raise AssertionError(f"chip {text!r} not found")


def test_history_chip_emits_the_query(qapp: QApplication) -> None:
    parent = QWidget()
    popup = SearchPopup(parent)
    popup.set_history(["dog", "cat"])

    seen: list[str] = []
    popup.query_selected.connect(seen.append)
    _chip(popup, "cat").click()

    assert seen == ["cat"]


def test_empty_history_shows_a_placeholder(qapp: QApplication) -> None:
    parent = QWidget()
    popup = SearchPopup(parent)
    popup.set_history([])

    labels = [label.text() for label in popup.findChildren(QLabel)]
    assert any("搜索记录" in text or "recent searches" in text.lower() for text in labels)


def test_clear_button_requests_history_clear(qapp: QApplication) -> None:
    parent = QWidget()
    popup = SearchPopup(parent)
    popup.set_history(["dog"])

    cleared: list[bool] = []
    popup.clear_history_requested.connect(lambda: cleared.append(True))
    popup._clear_history.click()

    assert cleared == [True]


def test_scope_switches_to_album_and_back(qapp: QApplication) -> None:
    parent = QWidget()
    popup = SearchPopup(parent)
    assert popup.current_scope() == "all"
    assert popup._scope_album.isEnabled() is False

    popup.set_album_scope("202406_a")
    assert popup._scope_album.isEnabled() is True

    popup._scope_album.click()
    assert popup.current_scope() == "album"

    popup.set_album_scope(None)
    assert popup.current_scope() == "all"
    assert popup._scope_album.isEnabled() is False

    # A checkable button toggles itself off when clicked while active; the
    # popup must keep the active scope checked.
    popup._scope_all.click()
    assert popup.current_scope() == "all"
    assert popup._scope_all.isChecked() is True


def test_similar_thumbnail_emits_asset_id(qapp: QApplication) -> None:
    parent = QWidget()
    popup = SearchPopup(parent)
    pixmap = QPixmap(8, 8)
    pixmap.fill(Qt.GlobalColor.red)
    popup.set_similar_photos([("asset-1", pixmap), ("asset-2", pixmap)])

    buttons = [
        popup._similar_row.itemAt(index).widget()
        for index in range(popup._similar_row.count())
    ]
    buttons = [button for button in buttons if button is not None]

    seen: list[str] = []
    popup.similar_selected.connect(seen.append)
    buttons[0].click()

    assert seen == ["asset-1"]
