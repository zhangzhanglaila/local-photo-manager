"""The first-run wizard lets the user pick the search model."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication

from iPhoto.agent.services import search_models


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def wizard(qapp: QApplication):
    from iPhoto.gui.ui.widgets.setup_wizard import SetupWizard

    dialog = SetupWizard()
    yield dialog
    dialog.close()
    dialog.deleteLater()


def test_ai_step_shows_the_current_model(wizard):
    wizard._step_list.setCurrentRow(3)

    text = wizard._model_label.text()
    assert search_models.resolve_spec(wizard.selected_search_model()).dir_name
    assert text  # "当前：<name> · 语言 · 质量 · 体积"
    assert str(search_models.resolve_spec(wizard.selected_search_model()).download_mb) in text


def test_choosing_a_model_updates_the_wizard(wizard):
    wizard._step_list.setCurrentRow(3)

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self):
            return 1

        def selected_key(self) -> str:
            return "siglip2-base"

    import iPhoto.gui.ui.widgets.setup_wizard as module

    original = module.SearchModelDialog
    module.SearchModelDialog = _AcceptedDialog
    try:
        wizard._choose_search_model()
    finally:
        module.SearchModelDialog = original

    assert wizard.selected_search_model() == "siglip2-base"
    wizard._step_list.setCurrentRow(2)
    wizard._step_list.setCurrentRow(3)
    wizard._capture_step()
    assert wizard.selected_search_model() == "siglip2-base"
