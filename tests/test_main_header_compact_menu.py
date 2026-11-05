"""The compact toolbar keeps the application menus reachable.

macOS puts application menus in the system menu bar, so the in-window menu bar was
replaced by a single "⋯" button. The button has to mirror the same menus — losing
an entry there would silently remove a feature.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QMainWindow

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
