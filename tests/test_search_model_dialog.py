"""The search-model chooser explains the trade-offs and returns a key."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QListWidgetItem

from iPhoto.agent.services import search_models
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


def test_dialog_lists_every_model_with_its_tradeoffs(qapp: QApplication):
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        keys = [
            dialog._list.item(row).data(0x0100)  # Qt.ItemDataRole.UserRole
            for row in range(dialog._list.count())
        ]
        assert keys == [spec.key for spec in search_models.available_models()]

        text = dialog._details.text()
        assert "MB" in text
        assert "language" in text.lower() or "语言" in text or "Languages" in text
    finally:
        dialog.close()


def test_selecting_another_model_returns_its_key(qapp: QApplication):
    dialog = SearchModelDialog(current_key="clip-b32")
    try:
        for row in range(dialog._list.count()):
            item: QListWidgetItem = dialog._list.item(row)
            if item.data(0x0100) == "siglip2-base":
                dialog._list.setCurrentItem(item)
                break
        assert dialog._use_button.isEnabled() is True
        dialog._accept_selection()
        assert dialog.selected_key() == "siglip2-base"
    finally:
        dialog.close()


def test_keeping_the_current_model_needs_no_action(qapp: QApplication):
    dialog = SearchModelDialog(current_key="siglip2-base")
    try:
        assert dialog._use_button.isEnabled() is False
        assert dialog.selected_key() == "siglip2-base"
    finally:
        dialog.close()


def test_summary_and_details_mention_size_and_state():
    spec = search_models.resolve_spec("siglip2-base")

    assert str(spec.download_mb) in model_summary(spec)
    details = model_details(spec)
    assert "SigLIP 2 Base" in details
    assert str(spec.download_mb) in details
