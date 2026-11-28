"""The settings window mirrors the live state of the application actions.

Every option that used to live in the toolbar's "⋯" menu is now a control in a
standalone window. The window is rebuilt on each open, so its controls must read
(and write) the very same ``QAction`` objects the rest of the window uses.
"""

from __future__ import annotations

import os
import threading
import time

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


def _wait_until(qapp, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


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


def test_settings_window_opens_without_waiting_for_slow_value_probes(
    chrome, monkeypatch
) -> None:
    """Opening 设置 must not import/query on the GUI thread and freeze the app."""

    from iPhoto.gui.ui.widgets import settings_dialog as module

    _window, header = chrome
    release = threading.Event()

    class _SlowMeta(module._MetaProbe):
        def run(self) -> None:  # noqa: D102 - test double
            release.wait(2.0)

    monkeypatch.setattr(module, "_MetaProbe", _SlowMeta)
    started = time.monotonic()
    dialog = SettingsDialog(header, None)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 1.5, "model/device probing must run off the GUI thread"
    finally:
        release.set()
        dialog.close()
        dialog.deleteLater()


def test_settings_navigation_uses_the_pointing_hand_cursor(chrome) -> None:
    from PySide6.QtCore import Qt

    _window, header = chrome
    dialog = SettingsDialog(header, None)
    try:
        assert dialog._nav.cursor().shape() == Qt.CursorShape.PointingHandCursor
    finally:
        dialog.close()
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


def test_rebuild_action_requests_a_full_reindex(chrome, monkeypatch, qapp):
    """The settings window cannot reach the coordinator, so it writes a request."""

    from PySide6.QtWidgets import QLabel

    from iPhoto.agent.services import search_models
    from iPhoto.gui.ui.widgets import dialogs

    window, header = chrome
    settings = type("_S", (), {"values": {}, "get": lambda self, k, d=None: self.values.get(k, d), "set": lambda self, k, v: self.values.__setitem__(k, v)})()
    monkeypatch.setattr(dialogs, "confirm_action", lambda *a, **kw: True)

    dialog = SettingsDialog(header, None, window, settings=settings)
    try:
        labels = {label.text() for label in dialog.findChildren(QLabel)}
        assert "重建搜索索引…" in labels or "Rebuild search index…" in labels

        action = dialog._rebuild_index_action()
        action.trigger()
        assert search_models.REBUILD_KEY in settings.values
    finally:
        dialog.close()
        dialog.deleteLater()


def test_refreshing_rows_does_not_fabricate_coverage(chrome, qapp):
    """Rendering the index row used to write a guessed value into the status object."""

    from iPhoto.gui.viewmodels.search_index_status import SearchIndexStatus

    window, header = chrome
    status = SearchIndexStatus()
    dialog = SettingsDialog(header, None, window, search_status=status)
    try:
        dialog._refresh_search_rows()

        assert status.done == 0
        assert status.total == 0
    finally:
        dialog.close()
        dialog.deleteLater()


def test_settings_rows_report_model_device_and_index_progress(chrome, qapp):
    """The window must say which model runs where and whether vectors are building."""

    from iPhoto.gui.viewmodels.search_index_status import SearchIndexStatus

    window, header = chrome
    status = SearchIndexStatus()
    dialog = SettingsDialog(header, None, window, search_status=status)
    try:
        status.set_device("cuda", "float16")
        status.set_indexing(True, 4200, 14806)
        _wait_until(qapp, lambda: "CUDA" in dialog._model_value_label.text())
        _wait_until(qapp, lambda: "28%" in dialog._index_state_label.text())

        model_text = dialog._model_value_label.text()
        # Only the device: "float16" is precision, not something to decode here.
        assert "CUDA" in model_text
        assert "float16" not in model_text
        assert "MB" not in model_text

        index_text = dialog._index_state_label.text()
        assert "28%" in index_text
        assert "4200" in index_text and "14806" in index_text

        status.set_indexing(False, 14806, 14806)
        _wait_until(
            qapp, lambda: "14806/14806" in dialog._index_state_label.text()
        )
        assert "14806/14806" in dialog._index_state_label.text()
    finally:
        status.set_indexing(False, 0, 0)
        status.set_device("", "")
        dialog.close()
        dialog.deleteLater()
