"""The compact toolbar keeps the application menus reachable.

macOS puts application menus in the system menu bar, so the in-window menu bar was
replaced by a single "⋯" button. The button has to mirror the same menus — losing
an entry there would silently remove a feature.
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


def test_window_menu_bar_is_hidden(chrome: tuple[QMainWindow, MainHeaderWidget]) -> None:
    _window, header = chrome

    assert header.menu_bar.isVisible() is False
    assert header.more_button.isVisible() is True


def test_compact_menu_is_filled_without_being_shown(
    chrome: tuple[QMainWindow, MainHeaderWidget],
) -> None:
    """The menu must be populated eagerly.

    Building it from ``aboutToShow`` produced an empty popup, because Qt sizes the
    popup before that signal is delivered.
    """

    _window, header = chrome

    compact_actions = header.more_menu.actions()

    assert compact_actions, "the compact menu must contain entries before being shown"
    assert [action.text() for action in compact_actions] == [
        action.text() for action in header.menu_bar.actions()
    ]


def test_compact_menu_mirrors_the_menu_bar(chrome: tuple[QMainWindow, MainHeaderWidget]) -> None:
    _window, header = chrome

    compact_actions = header.more_menu.actions()
    bar_actions = header.menu_bar.actions()

    for compact, original in zip(compact_actions, bar_actions):
        assert compact.menu() is not None
        compact_children = [
            action.text() for action in compact.menu().actions() if not action.isSeparator()
        ]
        original_children = [
            action.text() for action in original.menu().actions() if not action.isSeparator()
        ]
        assert compact_children == original_children


def test_compact_menu_survives_retranslation(
    chrome: tuple[QMainWindow, MainHeaderWidget],
) -> None:
    """Reusing the menus must not let Qt delete them.

    An earlier implementation copied the actions into fresh submenus, which
    destroyed the source menus and left the popup empty.
    """

    _window, header = chrome

    header.retranslate()

    assert [action.menu().title() for action in header.more_menu.actions()] == [
        action.text() for action in header.menu_bar.actions()
    ]
    for action in header.more_menu.actions():
        assert action.menu() is not None
        assert action.menu().actions()


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

