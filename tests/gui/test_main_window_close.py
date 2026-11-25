"""Closing the window asks minimise-or-quit and can remember the answer.

The prompt used to be a native ``QMessageBox``: unstyled, and unable to remember
the user's choice. It is now the reference card, and the remembered choice lives
in the settings so the Settings window can change it again.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QMainWindow

from iPhoto.gui.ui.main_window import MainWindow
from iPhoto.gui.ui.widgets.close_dialog import CloseDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _Settings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value


def _host(settings: _Settings) -> QMainWindow:
    """A widget stand-in: the prompt only needs a parent and the settings."""

    window = QMainWindow()
    window._context = SimpleNamespace(settings=settings)
    return window


def _press(monkeypatch, choice: str, remember: bool) -> None:
    def fake_exec(dialog: CloseDialog) -> int:
        dialog.choice = choice
        dialog.remember = remember
        return 1

    monkeypatch.setattr(CloseDialog, "exec", fake_exec)


@pytest.mark.parametrize(
    ("choice", "remember"),
    [("minimise", False), ("quit", False), ("cancel", False)],
)
def test_prompt_returns_the_pressed_option(monkeypatch, qapp, choice, remember) -> None:
    settings = _Settings()
    window = _host(settings)
    _press(monkeypatch, choice, remember)

    result = MainWindow._ask_close_action(window)

    assert result == choice
    assert "ui.close_action" not in settings.values


def test_remembering_the_choice_persists_it(monkeypatch, qapp) -> None:
    settings = _Settings()
    window = _host(settings)
    _press(monkeypatch, "quit", True)

    assert MainWindow._ask_close_action(window) == "quit"
    assert settings.values["ui.close_action"] == "quit"


def test_remembered_choice_skips_the_prompt(monkeypatch, qapp) -> None:
    settings = _Settings({"ui.close_action": "minimise"})
    window = _host(settings)

    def fail_exec(dialog: CloseDialog) -> int:
        raise AssertionError("the prompt must not open once a choice is remembered")

    monkeypatch.setattr(CloseDialog, "exec", fail_exec)

    assert MainWindow._ask_close_action(window) == "minimise"


def test_dialog_offers_both_options_and_a_remember_box(qapp) -> None:
    dialog = CloseDialog()
    try:
        assert dialog.minimise_card.isEnabled()
        assert dialog.quit_card.isEnabled()
        assert dialog.remember_box.isChecked() is False
        assert dialog.choice == "cancel"
    finally:
        dialog.deleteLater()
