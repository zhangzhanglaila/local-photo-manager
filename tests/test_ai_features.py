"""Switches for the two on-device AI scans, and their settings-page wiring."""

from __future__ import annotations

import os
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


def _library_with_photos(tmp_path: Path, statuses: list[str]) -> Path:
    """Build a real asset index so coverage reads the production schema."""

    from iPhoto.cache.index_store import IndexStore

    store = IndexStore(tmp_path)
    try:
        store.write_rows(
            [
                {
                    "rel": f"p{i}.jpg",
                    "id": f"p{i}",
                    "media_type": 0,
                    "live_role": 0,
                    "face_status": status,
                }
                for i, status in enumerate(statuses)
            ]
        )
    finally:
        store.close()
    return tmp_path


@pytest.fixture()
def library(tmp_path) -> Path:
    return tmp_path


def test_skipped_photos_count_as_finished(library: Path):
    _library_with_photos(library, ["done", "done", "skipped", "pending"])

    assert ai_coverage.face_coverage(library) == (3, 4)


def test_no_database_reports_nothing(library: Path):
    """Opening 设置 must not create an index for a library that was never scanned."""

    assert ai_coverage.face_coverage(library) == (0, 0)
    assert not (library / ".iPhoto" / "global_index.db").exists()


def test_ocr_coverage_reads_the_processed_table(library: Path):
    _library_with_photos(library, ["done"] * 5)
    from iPhoto.ai.db.ocr_database import OCRRepository

    ocr = OCRRepository(library)
    try:
        ocr.mark_processed("a", "a.jpg")
        ocr.mark_processed("b", "b.jpg")
    finally:
        ocr.close()

    assert ai_coverage.ocr_coverage(library) == (2, 5)


def test_missing_ocr_database_reports_zero_of_the_library(library: Path):
    _library_with_photos(library, ["done"] * 4)

    assert ai_coverage.ocr_coverage(library) == (0, 4)


def test_people_count_uses_the_face_index(library: Path):
    import numpy as np

    from iPhoto.people.records import FaceRecord, PersonRecord
    from iPhoto.people.service import PeopleService

    assert ai_coverage.people_count(library) == 0

    people = PeopleService(library)
    repository = people.repository()
    assert repository is not None
    repository.replace_all(
        faces=[
            FaceRecord(
                face_id="face-1",
                face_key="key-1",
                asset_id="p0",
                asset_rel="p0.jpg",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                confidence=0.9,
                embedding=np.zeros(4, dtype=np.float32),
                embedding_dim=4,
                thumbnail_path=None,
                person_id="person-1",
                detected_at="2026-01-01T00:00:00",
                image_width=100,
                image_height=100,
            )
        ],
        persons=[
            PersonRecord(
                person_id="person-1",
                name="Ada",
                key_face_id="face-1",
                face_count=1,
                center_embedding=np.zeros(4, dtype=np.float32),
                created_at="2026-01-01T00:00:00",
                updated_at="2026-01-01T00:00:00",
            )
        ],
        sync_runtime_state=False,
    )

    assert ai_coverage.people_count(library) == 1


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

    for expected in (
        "启用人脸识别",
        "人脸模型",
        "人脸索引状态",
        "补全人脸索引",
        "启用 OCR",
        "OCR 模型",
        "OCR 索引状态",
        "补全 OCR 索引",
    ):
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


def test_model_rows_show_the_active_pack(dialog):
    """Face and OCR present their model like 搜索模型: a value row, not a dropdown."""

    from PySide6.QtWidgets import QComboBox

    widget, _settings = dialog
    page = widget._pages[4]

    face_row = widget._pack_labels["face"].text()
    ocr_row = widget._pack_labels["ocr"].text()
    assert "buffalo_l" in face_row
    assert "PP-OCRv5" in ocr_row
    # Rows show name + device only; the size lives in the chooser dialog.
    for row in (face_row, ocr_row):
        assert "MB" not in row
        assert row.endswith(("CPU", "CUDA", "未下载"))


def test_the_model_dialogs_report_size_and_device(dialog):
    from iPhoto.agent.services import face_models, ocr_models
    from iPhoto.agent.services.ai_devices import onnx_device

    widget, _settings = dialog
    face_details = widget._pack_details("face", face_models.active_model())
    ocr_details = widget._pack_details("ocr", ocr_models.active_model())

    for details in (face_details, ocr_details):
        assert "MB" in details
        assert onnx_device() in details


def test_only_the_precision_row_is_still_a_dropdown(dialog):
    from PySide6.QtWidgets import QComboBox

    widget, _settings = dialog

    assert len(widget._pages[4].findChildren(QComboBox)) == 1


def test_switching_the_face_pack_goes_through_the_chooser(dialog, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from iPhoto.agent.services import face_models
    from iPhoto.gui.ui.widgets import model_chooser_dialog as chooser

    widget, settings = dialog
    seen: dict[str, object] = {}

    class _FakeDialog(chooser.ModelChooserDialog):
        def exec(self):  # noqa: D102 - test double
            seen["options"] = [option.key for option in self._options]
            self._selected_key = "buffalo_s"
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(chooser, "ModelChooserDialog", _FakeDialog)

    widget._switch_packs_action("face").trigger()

    assert seen["options"] == ["buffalo_l", "buffalo_s"]
    assert settings.values[face_models.SETTINGS_KEY] == "buffalo_s"
    assert face_models.active_model().key == "buffalo_s"
    assert "buffalo_s" in widget._pack_labels["face"].text()
    face_models.set_active_model("buffalo_l")


def test_chooser_dialog_shows_every_pack_without_inner_scrolling(qapp):
    from iPhoto.gui.ui.widgets.model_chooser_dialog import (
        ModelChooserDialog,
        ModelOption,
    )

    options = [
        ModelOption(key="a", title="Pack A", summary="1 MB", details="a"),
        ModelOption(key="b", title="Pack B", summary="2 MB", details="b"),
    ]
    dialog = ModelChooserDialog(None, title="t", subtitle="s", options=options, current_key="a")
    try:
        # All rows fit: the list has no scrollbar of its own.
        assert dialog._list.count() == 2
        assert dialog._list.verticalScrollBarPolicy().name == "ScrollBarAlwaysOff"
    finally:
        dialog.close()


def test_face_registry_falls_back_to_an_installed_pack(monkeypatch):
    """Never point the scan at a pack that is not on disk."""

    from iPhoto.agent.services import face_models

    monkeypatch.setattr(
        face_models, "is_installed", lambda spec: spec.key == "buffalo_s"
    )

    assert face_models.set_active_model("buffalo_l").key == "buffalo_s"
    face_models.set_active_model("buffalo_l")  # restore for other tests
    monkeypatch.undo()
    face_models.set_active_model("buffalo_l")


def test_ocr_registry_discovers_packs_on_disk(tmp_path, monkeypatch):
    from iPhoto.agent.services import ocr_models

    pack = tmp_path / "ocr-ppocr-v4"
    pack.mkdir()
    (pack / "ch_PP-OCRv4_det_infer.onnx").write_bytes(b"x" * 100)
    (pack / "ch_PP-OCRv4_rec_infer.onnx").write_bytes(b"x" * 200)
    from iPhoto.agent.services import model_paths

    monkeypatch.setattr(model_paths, "install_root", lambda: tmp_path)
    monkeypatch.setattr(model_paths, "legacy_roots", lambda: ())

    specs = ocr_models.available_models()
    keys = [spec.key for spec in specs]

    assert keys[0] == ocr_models.BUILTIN_KEY
    assert "ocr-ppocr-v4" in keys
    discovered = ocr_models.resolve_spec("ocr-ppocr-v4")
    assert discovered.det is not None and discovered.rec is not None
    assert ocr_models.is_installed(discovered)


def test_ocr_pack_without_detector_is_ignored(tmp_path, monkeypatch):
    from iPhoto.agent.services import ocr_models

    pack = tmp_path / "ocr-broken"
    pack.mkdir()
    (pack / "ch_PP-OCRv4_rec_infer.onnx").write_bytes(b"x")
    from iPhoto.agent.services import model_paths

    monkeypatch.setattr(model_paths, "install_root", lambda: tmp_path)
    monkeypatch.setattr(model_paths, "legacy_roots", lambda: ())

    assert [spec.key for spec in ocr_models.available_models()] == [
        ocr_models.BUILTIN_KEY
    ]


def test_ocr_engine_passes_paths_for_a_disk_pack(tmp_path):
    from iPhoto.ai.ocr.ocr_engine import OCREngine
    from iPhoto.agent.services.ocr_models import OCRModelSpec

    det = tmp_path / "det.onnx"
    rec = tmp_path / "rec.onnx"
    spec = OCRModelSpec(
        key="custom",
        title="custom",
        total_mb=1.0,
        directory=tmp_path,
        det=det,
        rec=rec,
    )

    params = OCREngine(spec=spec)._engine_params()

    assert params["Det.model_path"] == str(det)
    assert params["Rec.model_path"] == str(rec)
    # The built-in pack asks for PP-OCRv5 mobile instead of overriding paths.
    default_params = OCREngine()._engine_params()
    assert "Det.model_path" not in default_params
    assert default_params["Det.ocr_version"].value == "PP-OCRv5"
    assert default_params["Rec.model_type"].value == "mobile"
