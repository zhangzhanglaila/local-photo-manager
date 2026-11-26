"""Switches for the two on-device AI scans, and their settings-page wiring."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from iPhoto.agent.services import ai_coverage, ai_features


class _Settings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    ai_features.set_settings_provider(None)


def test_both_scans_default_to_on_without_settings():
    ai_features.set_settings_provider(None)

    assert ai_features.face_enabled() is True
    assert ai_features.ocr_enabled() is True


def test_switches_follow_the_settings_manager():
    settings = _Settings({ai_features.FACE_KEY: False, ai_features.OCR_KEY: True})
    ai_features.set_settings_provider(lambda: settings)

    assert ai_features.face_enabled() is False
    assert ai_features.ocr_enabled() is True

    ai_features.set_face_enabled(True)
    assert settings.values[ai_features.FACE_KEY] is True


def test_a_broken_provider_does_not_break_scanning():
    def explode():
        raise RuntimeError("settings unavailable")

    ai_features.set_settings_provider(explode)

    # Scanning must keep working rather than dying on a settings read.
    assert ai_features.face_enabled() is True
    assert ai_features.ocr_enabled() is True


def _index_db(tmp_path: Path, statuses: list[str]) -> Path:
    db = tmp_path / "global_index.db"
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            "CREATE TABLE assets (id TEXT, media_type INTEGER, live_role INTEGER,"
            " face_status TEXT)"
        )
        connection.executemany(
            "INSERT INTO assets VALUES (?, 0, 0, ?)",
            [(f"p{i}", status) for i, status in enumerate(statuses)],
        )
        connection.commit()
    finally:
        connection.close()
    return db


@pytest.fixture()
def library(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(ai_coverage, "_index_db", lambda root: tmp_path / "global_index.db")
    monkeypatch.setattr(ai_coverage, "_work_dir", lambda root: tmp_path)
    return tmp_path


def test_skipped_photos_count_as_finished(library: Path):
    _index_db(library, ["done", "done", "skipped", "pending"])

    assert ai_coverage.face_coverage(library) == (3, 4)


def test_no_database_reports_nothing(library: Path):
    assert ai_coverage.face_coverage(library) == (0, 0)


def test_ocr_coverage_reads_the_processed_table(library: Path):
    _index_db(library, ["done"] * 5)
    ocr = sqlite3.connect(str(library / "ocr_index.db"))
    try:
        ocr.execute("CREATE TABLE ocr_processed (asset_id TEXT)")
        ocr.executemany("INSERT INTO ocr_processed VALUES (?)", [("a",), ("b",)])
        ocr.commit()
    finally:
        ocr.close()

    assert ai_coverage.ocr_coverage(library) == (2, 5)


def test_missing_ocr_database_reports_zero_of_the_library(library: Path):
    _index_db(library, ["done"] * 4)

    assert ai_coverage.ocr_coverage(library) == (0, 4)


# --- settings page ---------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from iPhoto.gui.ui.widgets.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def dialog(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMainWindow

    from iPhoto.gui.ui.widgets import dialogs
    from iPhoto.gui.ui.widgets.main_header import MainHeaderWidget

    library = SimpleNamespace(root=lambda: tmp_path)
    window = QMainWindow()
    header = MainHeaderWidget(window, window)
    window.setCentralWidget(header)
    window.show()
    qapp.processEvents()

    settings = _Settings()
    widget = SettingsDialog(header, library, window, settings=settings)
    monkeypatch.setattr(dialogs, "confirm_action", lambda *a, **kw: True)
    try:
        yield widget, settings
    finally:
        widget.close()
        widget.deleteLater()
        window.close()
        window.deleteLater()


def _labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


def test_smart_page_shows_face_and_text_rows(dialog):
    widget, _settings = dialog
    widget._nav.setCurrentRow(4)
    text = " ".join(_labels(widget))

    for expected in ("人脸识别", "人脸索引状态", "补全人脸索引", "文字识别", "文字索引状态", "补全文字索引"):
        assert expected in text, expected


def test_toggling_writes_the_switch(dialog):
    widget, settings = dialog

    widget._set_ai_switch(ai_features.FACE_KEY, False)

    assert settings.values[ai_features.FACE_KEY] is False
    widget._set_ai_switch(ai_features.OCR_KEY, False)
    assert settings.values[ai_features.OCR_KEY] is False


def test_rebuild_actions_request_a_scan(dialog):
    widget, settings = dialog

    for feature, key in (
        ("face", ai_features.FACE_REBUILD_KEY),
        ("ocr", ai_features.OCR_REBUILD_KEY),
    ):
        action = widget._ai_rebuild_action(feature)
        action.trigger()
        assert key in settings.values, feature


def test_state_row_reports_coverage(dialog, monkeypatch):
    widget, _settings = dialog
    monkeypatch.setattr(
        ai_coverage, "face_coverage", lambda root: (12, 40), raising=True
    )
    monkeypatch.setattr(ai_coverage, "people_count", lambda root: 3, raising=True)

    widget._refresh_ai_rows()

    assert "12/40" in widget._ai_state_labels["face"].text()
