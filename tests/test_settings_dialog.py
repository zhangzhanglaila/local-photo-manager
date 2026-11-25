"""The settings window mirrors the live state of the application actions.

Every option that used to live in the toolbar's "⋯" menu is now a control in a
standalone window. The window is rebuilt on each open, so its controls must read
(and write) the very same ``QAction`` objects the rest of the window uses.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QMainWindow

from iPhoto.gui.ui.widgets.main_header import MainHeaderWidget
from iPhoto.gui.ui.widgets.settings_dialog import SettingsDialog


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def chrome(qapp: QApplication):
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


def test_settings_window_has_one_page_per_category(chrome) -> None:
    _window, header = chrome

    dialog = SettingsDialog(header, None)
    try:
        assert dialog._nav.count() == dialog._stack.count()
        titles = [dialog._nav.item(row).text() for row in range(dialog._nav.count())]
        assert titles == ["通用", "浏览", "导出", "图库", "智能"]
    finally:
        dialog.deleteLater()


def test_choice_controls_drive_the_shared_actions(chrome) -> None:
    _window, header = chrome

    dialog = SettingsDialog(header, None)
    try:
        general = dialog._pages[0]
        theme_combo = general.findChildren(QComboBox)[0]

        # The combo starts on the action that is currently checked…
        assert header.theme_group.actions()[theme_combo.currentIndex()].isChecked()

        # …and picking another entry checks that action (and only that one).
        theme_combo.setCurrentIndex(2)
        assert header.theme_dark.isChecked()
        assert header.theme_system.isChecked() is False
    finally:
        dialog.deleteLater()


def test_toggle_controls_drive_the_shared_actions(chrome) -> None:
    _window, header = chrome

    dialog = SettingsDialog(header, None)
    try:
        browsing = dialog._pages[1]
        filmstrip = browsing.findChildren(QCheckBox)[0]

        assert filmstrip.isChecked() == header.toggle_filmstrip_action.isChecked()

        filmstrip.setChecked(not filmstrip.isChecked())
        assert header.toggle_filmstrip_action.isChecked() == filmstrip.isChecked()
    finally:
        dialog.deleteLater()


class _MemorySettings:
    def __init__(self) -> None:
        self.values: dict = {}

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value


def test_close_behaviour_preference_is_editable(chrome) -> None:
    """The "do not ask again" choice must be reversible from Settings."""

    _window, header = chrome
    settings = _MemorySettings()

    dialog = SettingsDialog(header, None, None, settings=settings)
    try:
        general = dialog._pages[0]
        combo = general.findChildren(QComboBox)[-1]

        combo.setCurrentIndex(2)
        assert settings.values["ui.close_action"] == "quit"

        combo.setCurrentIndex(0)
        assert settings.values["ui.close_action"] is None
    finally:
        dialog.deleteLater()


def test_settings_window_follows_the_selected_language(chrome, qapp):
    """The window used to hard-code Chinese labels, so an English UI stayed bilingual."""

    from PySide6.QtWidgets import QLabel

    from iPhoto.i18n import set_language

    window, header = chrome
    try:
        set_language("en")
        dialog = SettingsDialog(header, None, window)
        labels = {label.text() for label in dialog.findChildren(QLabel)}
        assert "Library" in labels, labels
        assert "Storage" in labels
        assert "更改存储位置…" not in labels
        assert "图库" not in labels
        dialog.close()
        dialog.deleteLater()
    finally:
        set_language("zh")
