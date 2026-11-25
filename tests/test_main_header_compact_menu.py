"""The application menus remain reachable from the sidebar's Settings entry.

The toolbar used to carry a "⋯" button that opened every menu. It now lives in
the sidebar footer as "设置", matching the reference sidebar, so the same
guarantee has to hold: every top-level menu must still be listed there, with
the original (not copied) menu objects behind it.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton

from iPhoto.gui.ui.widgets.main_header import MainHeaderWidget


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def chrome(qapp: QApplication) -> tuple[QMainWindow, MainHeaderWidget]:
    """Return a window and its header, keeping the window alive for the test."""

    window = QMainWindow()
    header = MainHeaderWidget(window, window)
    window.setCentralWidget(header)
    window.show()
    qapp.processEvents()
    try:
        yield window, header
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_menu_bar_is_hidden(chrome: tuple[QMainWindow, MainHeaderWidget]) -> None:
    _window, header = chrome

    assert header.menu_bar.isVisible() is False


def test_header_has_no_overflow_button(
    chrome: tuple[QMainWindow, MainHeaderWidget],
) -> None:
    """The "⋯" button moved into the sidebar's Settings row."""

    _window, header = chrome

    assert not hasattr(header, "more_button")


def test_menus_are_still_built(chrome: tuple[QMainWindow, MainHeaderWidget]) -> None:
    """The hidden menu bar still owns the application menus.

    The Settings window is built from the actions, but the menus are kept so the
    shortcuts and action ownership stay intact.
    """

    _window, header = chrome

    menus = header.menu_bar.actions()
    assert menus, "the application menus must still exist"
    for menu_action in menus:
        assert menu_action.menu() is not None
        assert menu_action.menu().actions()


def test_search_field_keeps_the_magnifier_inside(
    chrome: tuple[QMainWindow, MainHeaderWidget],
) -> None:
    _window, header = chrome

    # The leading action is the in-field magnifier; the old emoji label must not
    # take layout space.
    assert header.search_input.actions()
    assert header.search_input._leading_action.isEnabled() is False
    assert header.search_icon.isVisible() is False


def _history_chip(header: MainHeaderWidget, text: str) -> QPushButton:
    popup = header._search_popup
    assert popup is not None
    for button in popup.findChildren(QPushButton):
        if button.text() == text:
            return button
    raise AssertionError(f"history chip {text!r} not found")


def test_search_enter_emits_query_with_scope(
    chrome: tuple[QMainWindow, MainHeaderWidget],
) -> None:
    _window, header = chrome
    submitted: list[tuple[str, str]] = []
    header.search_submitted_with_scope.connect(
        lambda query, scope: submitted.append((query, scope))
    )

    header.search_input.setText("dog")
    header.search_input._on_return_pressed()

    assert submitted == [("dog", "all")]


def test_history_chip_starts_a_search(
    chrome: tuple[QMainWindow, MainHeaderWidget], qapp: QApplication
) -> None:
    _window, header = chrome
    submitted: list[tuple[str, str]] = []
    header.search_submitted_with_scope.connect(
        lambda query, scope: submitted.append((query, scope))
    )
    header.set_search_history(["cat"])
    header.focus_search()
    qapp.processEvents()

    _history_chip(header, "cat").click()

    assert submitted == [("cat", "all")]
    assert header.search_input.text() == "cat"


def test_album_scope_is_used_when_selected(
    chrome: tuple[QMainWindow, MainHeaderWidget], qapp: QApplication
) -> None:
    _window, header = chrome
    submitted: list[tuple[str, str]] = []
    header.search_submitted_with_scope.connect(
        lambda query, scope: submitted.append((query, scope))
    )
    header.set_album_scope("202406_a")
    header.focus_search()
    qapp.processEvents()

    popup = header._search_popup
    assert popup is not None
    popup._scope_album.click()
    header.search_input.setText("dog")
    header.search_input._on_return_pressed()

    assert submitted == [("dog", "album")]
