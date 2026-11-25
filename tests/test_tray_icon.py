"""Tray presence: the window can be hidden and brought back from the tray.

The reference application keeps a tray entry whose context menu can reopen the
window, open settings, or quit. Closing the window with "minimise" therefore
hides it instead of dropping to the taskbar alone.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from iPhoto.gui.ui.main_window import MainWindow
from iPhoto.gui.ui.widgets.tray_icon import AppTrayIcon


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_tray_menu_offers_the_expected_entries(qapp: QApplication) -> None:
    # Keep the host widget alive: it owns the tray icon through Qt's parent chain.
    host = QWidget()
    tray = AppTrayIcon(host, QIcon())
    try:
        labels = [action.text() for action in tray._menu.actions() if not action.isSeparator()]
        assert labels == ["打开主窗口", "设置…", "退出 iPhoto"]

        fired: list[str] = []
        tray.quit_requested.connect(lambda: fired.append("quit"))
        tray._menu.actions()[-1].trigger()
        assert fired == ["quit"]
    finally:
        tray.hide()
        tray.deleteLater()


def test_activating_the_tray_icon_restores_the_window(qapp: QApplication) -> None:
    host = QWidget()
    tray = AppTrayIcon(host, QIcon())
    try:
        fired: list[str] = []
        tray.show_requested.connect(lambda: fired.append("show"))
        tray._on_activated(tray._tray.ActivationReason.Trigger)
        assert fired == ["show"]
    finally:
        tray.hide()
        tray.deleteLater()


class _FakeTray:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def show_message(self, title: str, message: str) -> None:
        self.messages.append((title, message))


def test_minimising_hides_the_window_when_a_tray_exists(qapp: QApplication) -> None:
    window = QMainWindow()
    window.show()
    qapp.processEvents()
    assert window.isVisible() is True

    tray = _FakeTray()
    window._tray = tray
    try:
        MainWindow.minimise_to_tray(window)
        assert window.isVisible() is False, "the window must leave the taskbar"
        assert tray.messages, "the user should be told where the app went"
    finally:
        window.close()
        window.deleteLater()


def test_minimising_without_a_tray_falls_back_to_the_taskbar(qapp: QApplication) -> None:
    window = QMainWindow()
    window.show()
    qapp.processEvents()

    window._tray = None
    MainWindow.minimise_to_tray(window)
    assert window.isMinimized() is True, "without a tray the window must stay reachable"

    window.close()
    window.deleteLater()
