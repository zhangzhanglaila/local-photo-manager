"""The search-model chooser explains the trade-offs, shows install state and
can download the weights (with a manual fallback when the network fails)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QListWidgetItem

from iPhoto.agent.services import search_models
from iPhoto.gui.ui.widgets import search_model_dialog as module
from iPhoto.gui.ui.widgets.search_model_dialog import (
    SearchModelDialog,
    model_details,
    model_summary,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _select(dialog: SearchModelDialog, key: str) -> None:
    for row in range(dialog._list.count()):
        item: QListWidgetItem = dialog._list.item(row)
        if item.data(0x0100) == key:
            dialog._list.setCurrentItem(item)
            return
    raise AssertionError(f"{key} missing from the list")


def test_dialog_lists_every_model_with_its_tradeoffs(qapp: QApplication, monkeypatch):
    monkeypatch.setattr(module.search_models, "is_installed", lambda spec, **kw: False)
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        keys = [
            dialog._list.item(row).data(0x0100) for row in range(dialog._list.count())
        ]
        assert keys == [spec.key for spec in search_models.available_models()]
        assert "MB" in dialog._details.text()
    finally:
        dialog.close()


def test_missing_model_offers_a_download_instead_of_silently_doing_nothing(
    qapp: QApplication, monkeypatch
):
    monkeypatch.setattr(module.search_models, "is_installed", lambda spec, **kw: False)
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        _select(dialog, "siglip2-base")
        assert dialog._download_button.isVisibleTo(dialog) is True
        assert dialog._use_button.isEnabled() is False
        assert dialog._use_button.text() == module.tr("search.model.download_and_use")
    finally:
        dialog.close()


def test_installed_model_can_be_selected(qapp: QApplication, monkeypatch):
    monkeypatch.setattr(module.search_models, "is_installed", lambda spec, **kw: True)
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        _select(dialog, "siglip2-base")
        assert dialog._use_button.isEnabled() is True
        assert dialog._download_button.isVisibleTo(dialog) is False
        dialog._accept_selection()
        assert dialog.selected_key() == "siglip2-base"
    finally:
        dialog.close()


def test_failed_download_points_at_the_folder(qapp: QApplication, monkeypatch):
    monkeypatch.setattr(module.search_models, "is_installed", lambda spec, **kw: False)
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        _select(dialog, "siglip2-base")
        dialog._on_download_finished(False, "huggingface.co unreachable")
        text = dialog._status.text()
        assert str(module.search_models.model_dir(dialog._spec())) in text
        assert "unreachable" in text
        assert dialog._download_button.isEnabled() is True
        assert dialog._download_button.text() == module.tr("search.model.retry")
    finally:
        dialog.close()


def test_successful_download_enables_use(qapp: QApplication, monkeypatch):
    state = {"installed": False}
    monkeypatch.setattr(
        module.search_models, "is_installed", lambda spec, **kw: state["installed"]
    )
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        _select(dialog, "siglip2-base")
        state["installed"] = True
        dialog._on_download_finished(True, "")
        assert dialog._use_button.isEnabled() is True
        assert dialog._progress_row.isVisibleTo(dialog) is False
    finally:
        dialog.close()


def test_summary_and_details_mention_size_and_state():
    spec = search_models.resolve_spec("siglip2-base")

    assert str(spec.download_mb) in model_summary(spec)
    details = model_details(spec)
    assert "SigLIP 2 Base" in details
    assert str(spec.download_mb) in details


def test_progress_reports_a_real_percentage(qapp: QApplication, monkeypatch):
    monkeypatch.setattr(module.search_models, "is_installed", lambda spec, **kw: False)
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        _select(dialog, "siglip2-base")
        dialog._progress_row.show()
        dialog._on_download_progress(0.42, "pytorch_model.bin")
        assert dialog._progress_bar.value() == 420
        assert "42" in dialog._progress_label.text()
    finally:
        dialog.close()


def test_failure_shows_clickable_model_pages(qapp: QApplication, monkeypatch):
    monkeypatch.setattr(module.search_models, "is_installed", lambda spec, **kw: False)
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        _select(dialog, "siglip2-base")
        dialog._on_download_finished(False, "<urlopen error [WinError 10060]>")
        assert dialog._links.isVisibleTo(dialog) is True
        text = dialog._links.text()
        assert "https://huggingface.co/google/siglip2-base-patch16-224" in text
        assert "https://modelscope.cn/models/google/siglip2-base-patch16-224" in text
        # The reason is spelled out instead of dumping the raw exception chain.
        assert "超时" in dialog._status.text() or "timed out" in dialog._status.text()
    finally:
        dialog.close()
