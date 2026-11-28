"""Switches for the two on-device AI scans, and their settings-page wiring."""

from __future__ import annotations

import os
import time
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


def test_both_scans_default_to_on_without_settings():
    from iPhoto.bootstrap.ai_feature_switches import SettingsAiFeatureSwitches

    switches = SettingsAiFeatureSwitches(_Settings())

    assert switches.face_enabled() is True
    assert switches.ocr_enabled() is True


def test_switches_follow_the_settings_manager():
    from iPhoto.bootstrap.ai_feature_switches import SettingsAiFeatureSwitches

    settings = _Settings({ai_features.FACE_KEY: False, ai_features.OCR_KEY: True})
    switches = SettingsAiFeatureSwitches(settings)

    assert switches.face_enabled() is False
    assert switches.ocr_enabled() is True

    switches.set_face_enabled(True)
    assert settings.values[ai_features.FACE_KEY] is True


def test_a_broken_settings_read_does_not_break_scanning():
    from iPhoto.bootstrap.ai_feature_switches import SettingsAiFeatureSwitches

    class _Broken:
        def get(self, key, default=None):
            raise RuntimeError("settings unavailable")

    switches = SettingsAiFeatureSwitches(_Broken())

    # Scanning must keep working rather than dying on a settings read.
    assert switches.face_enabled() is True
    assert switches.ocr_enabled() is True


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


def test_ocr_repository_groups_lines_per_photo(library: Path):
    from iPhoto.ai.db.ocr_database import OCRRepository
    from iPhoto.ai.ocr.models import OCRRegion

    repository = OCRRepository(library)
    try:
        repository.store_regions(
            "a",
            "a.jpg",
            [
                OCRRegion("Second", 0.9, 0, 20, 10, 5),
                OCRRegion("First", 0.8, 0, 5, 10, 5),
            ],
        )
        repository.store_regions(
            "b",
            "b.jpg",
            [OCRRegion("Other", 0.7, 0, 1, 10, 5)],
        )
        assets = repository.list_assets_with_text()
        assert [(item.asset_id, item.asset_rel) for item in assets] == [
            ("a", "a.jpg"),
            ("b", "b.jpg"),
        ]
        assert assets[0].text == "First\nSecond", "lines follow their vertical order"
        assert repository.count_assets() == 2
    finally:
        repository.close()


def test_ocr_search_finds_cjk_substrings(library: Path):
    """FTS5 stores a run of Chinese as one token; search must still find words."""

    from iPhoto.ai.db.ocr_database import OCRRepository
    from iPhoto.ai.ocr.models import OCRRegion

    repository = OCRRepository(library)
    try:
        repository.store_regions(
            "cjk",
            "cjk.jpg",
            [OCRRegion("莉莉牛杂", 0.9, 0, 0, 10, 5)],
        )
        assert [hit.asset_id for hit in repository.search("牛杂")] == ["cjk"]
        assert [hit.asset_id for hit in repository.search("莉莉")] == ["cjk"]
        assert [hit.asset_id for hit in repository.search("莉莉牛杂")] == ["cjk"]
        # Punctuation must not abort the search with an FTS syntax error.
        repository.search("莉莉-牛杂")
    finally:
        repository.close()


def test_ocr_search_falls_back_for_english_substrings(library: Path):
    from iPhoto.ai.db.ocr_database import OCRRepository
    from iPhoto.ai.ocr.models import OCRRegion

    repository = OCRRepository(library)
    try:
        repository.store_regions(
            "en",
            "en.jpg",
            [OCRRegion("MAKE LIFE ELECTRIC", 0.9, 0, 0, 10, 5)],
        )
        assert [hit.asset_id for hit in repository.search("LIFE")] == ["en"]
    finally:
        repository.close()


def test_ocr_results_page_lists_photos_and_their_text(qapp, tmp_path):
    from iPhoto.ai.db.ocr_database import OCRRepository
    from iPhoto.ai.ocr.models import OCRRegion
    from iPhoto.gui.ui.widgets.ocr_results_page import OcrResultsPage

    from PySide6.QtGui import QColor, QImage

    for name in ("a.jpg", "b.jpg"):
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("gray"))
        assert image.save(str(tmp_path / name))
    repository = OCRRepository(tmp_path)
    try:
        repository.store_regions(
            "a", "a.jpg", [OCRRegion("Hello", 0.9, 0, 0, 10, 5)]
        )
        repository.store_regions(
            "b", "b.jpg", [OCRRegion("World", 0.8, 0, 0, 10, 5)]
        )
    finally:
        repository.close()

    page = OcrResultsPage(tmp_path)
    page.reload()
    try:
        _wait_until(qapp, lambda: page._model.loaded_count() == 2)
        assert [
            page._model.asset_at(i).asset_rel
            for i in range(page._model.loaded_count())
        ] == ["a.jpg", "b.jpg"]
        assert "Hello" in page._text.toPlainText()
        _wait_until(qapp, lambda: page._preview.pixmap() is not None)
        assert page._preview.pixmap() is not None
        assert page._preview._wheel_action == "zoom"

        page._list.setCurrentIndex(page._model.index(1, 0))
        _wait_until(qapp, lambda: "b.jpg" in page._preview_cache)
        qapp.processEvents()
        assert "World" in page._text.toPlainText()

        from PySide6.QtTest import QSignalSpy

        back = QSignalSpy(page.backRequested)
        page.back_button.click()
        assert back.count() == 1
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_ocr_results_page_pages_through_every_asset(qapp, tmp_path):
    """The virtualised list must be able to reach the last OCR asset."""

    from PySide6.QtCore import QModelIndex

    from iPhoto.ai.db.ocr_database import OCRRepository
    from iPhoto.ai.ocr.models import OCRRegion
    from iPhoto.gui.ui.widgets.ocr_results_page import (
        OCR_RESULTS_PAGE_SIZE,
        OcrResultsPage,
    )

    total = OCR_RESULTS_PAGE_SIZE + 75
    repository = OCRRepository(tmp_path)
    try:
        repository.store_regions_batch(
            [
                (
                    f"id{i}",
                    f"p{i:03}.jpg",
                    [OCRRegion("text", 0.9, 0, 0, 10, 5)],
                    10,
                    5,
                )
                for i in range(total)
            ]
        )
    finally:
        repository.close()

    page = OcrResultsPage(tmp_path)
    page.reload()
    try:
        _wait_until(
            qapp,
            lambda: page._model.loaded_count() == OCR_RESULTS_PAGE_SIZE,
        )
        assert page._model.total() == total
        assert page._model.canFetchMore(QModelIndex())

        page._model.fetchMore(QModelIndex())
        _wait_until(qapp, lambda: page._model.loaded_count() == total)
        assert not page._model.canFetchMore(QModelIndex())

        ids = {
            page._model.asset_at(i).asset_id
            for i in range(page._model.loaded_count())
        }
        assert len(ids) == total, "paging must not repeat or skip assets"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


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


def _wait_until(qapp, predicate, timeout: float = 5.0) -> None:
    """Settings values are probed off-thread; let Qt deliver their signal."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


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


def test_scrolling_suspends_the_expensive_card_shadow(dialog, qapp):
    """The drop shadow is re-blurred on every repaint; scrolling pauses it."""

    widget, _settings = dialog
    widget._on_page_scrolled(0)
    assert not widget._shadow_effect.isEnabled()
    _wait_until(qapp, lambda: widget._shadow_effect.isEnabled())


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


def test_state_row_reports_coverage(dialog, monkeypatch, qapp):
    widget, _settings = dialog
    monkeypatch.setattr(
        ai_coverage, "face_coverage", lambda root: (12, 40), raising=True
    )
    monkeypatch.setattr(ai_coverage, "people_count", lambda root: 3, raising=True)

    widget._refresh_ai_rows()
    _wait_until(qapp, lambda: "12/40" in widget._ai_state_labels["face"].text())

    assert "12/40" in widget._ai_state_labels["face"].text()


def test_state_row_keeps_moving_while_the_dialog_is_open(dialog, monkeypatch, qapp):
    """The settings page used to read coverage once, at construction time."""

    widget, _settings = dialog
    coverage = {"face": (10, 40), "ocr": (5, 40)}
    monkeypatch.setattr(
        ai_coverage, "face_coverage", lambda root: coverage["face"], raising=True
    )
    monkeypatch.setattr(
        ai_coverage, "ocr_coverage", lambda root: coverage["ocr"], raising=True
    )
    monkeypatch.setattr(ai_coverage, "people_count", lambda root: 0, raising=True)

    widget._refresh_ai_rows()
    _wait_until(qapp, lambda: "10/40" in widget._ai_state_labels["face"].text())
    _wait_until(qapp, lambda: "5/40" in widget._ai_state_labels["ocr"].text())

    coverage.update(face=(25, 40), ocr=(20, 40))
    widget._refresh_ai_rows()
    _wait_until(qapp, lambda: "25/40" in widget._ai_state_labels["face"].text())
    _wait_until(qapp, lambda: "20/40" in widget._ai_state_labels["ocr"].text())

    # A visible settings window polls; otherwise the counts would freeze again.
    assert widget._ai_refresh_timer.interval() == 1000


def test_model_rows_show_the_active_pack(dialog, qapp):
    """Face and OCR present their model like 搜索模型: a value row, not a dropdown."""

    from PySide6.QtWidgets import QComboBox

    widget, _settings = dialog
    page = widget._pages[4]
    # Probes start lazily when 智能 is opened, not while the dialog is hidden.
    widget._nav.setCurrentRow(4)

    _wait_until(qapp, lambda: "buffalo_l" in widget._pack_labels["face"].text())
    _wait_until(qapp, lambda: "PP-OCRv5" in widget._pack_labels["ocr"].text())
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
    face_details = widget._pack_details("face", face_models.resolve_spec(None))
    ocr_details = widget._pack_details("ocr", ocr_models.resolve_spec(None))

    for details in (face_details, ocr_details):
        assert "MB" in details
        assert onnx_device() in details


def test_ocr_results_action_opens_a_page_with_a_back_button(dialog):
    widget, _settings = dialog

    widget._open_ocr_results_action().trigger()

    assert widget._ocr_results_page is not None
    assert widget._stack.currentWidget() is widget._ocr_results_page
    assert widget._ocr_results_page.back_button.isVisibleTo(widget)

    widget._close_ocr_results()
    assert widget._stack.currentWidget() is widget._pages[4]


def test_only_the_precision_row_is_still_a_dropdown(dialog):
    from PySide6.QtWidgets import QComboBox

    widget, _settings = dialog

    assert len(widget._pages[4].findChildren(QComboBox)) == 1


def test_switching_the_face_pack_goes_through_the_chooser(dialog, monkeypatch, qapp):
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
    assert widget._active_spec("face").key == "buffalo_s"
    _wait_until(qapp, lambda: "buffalo_s" in widget._pack_labels["face"].text())


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


def test_selection_falls_back_to_an_installed_face_pack(monkeypatch):
    """Never point the scan at a pack that is not on disk."""

    from iPhoto.agent.services import face_models
    from iPhoto.agent.services.model_selection import ModelSelection

    monkeypatch.setattr(
        face_models, "is_installed", lambda spec: spec.key == "buffalo_s"
    )
    selection = ModelSelection()

    assert selection.apply("face", "buffalo_l").key == "buffalo_s"


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
