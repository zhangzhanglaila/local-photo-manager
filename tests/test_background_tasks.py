"""Background-task registry and the status-bar popup that lists it."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QLabel, QProgressBar

from iPhoto.gui.services import background_tasks as registry_module
from iPhoto.gui.services.background_tasks import BackgroundTaskRegistry
from iPhoto.gui.ui.widgets.task_status_widget import (
    SmoothProgressBar,
    TaskPopup,
    TaskRow,
    TaskStatusButton,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def registry(monkeypatch) -> BackgroundTaskRegistry:
    reg = BackgroundTaskRegistry()
    monkeypatch.setattr(registry_module, "_registry", reg)
    return reg


def test_a_scan_and_an_index_build_coexist(registry):
    """The old single-line status bar lost one task when the other started."""

    registry.start("scan", "Scanning files")
    registry.update("scan", done=950, total=1000)
    registry.start("index", "Building the search index")
    registry.update("index", done=10, total=100)

    running = registry.running_tasks()
    assert {task.id for task in running} == {"scan", "index"}
    assert registry.task("scan").percent == 95
    assert registry.task("index").percent == 10


def test_finishing_one_task_keeps_the_other(registry):
    registry.start("scan", "Scanning files")
    registry.start("index", "Building the search index")

    registry.finish("scan")

    assert [task.id for task in registry.running_tasks()] == ["index"]
    assert registry.task("scan").state == "done"


def test_unknown_totals_are_indeterminate(registry):
    registry.start("scan", "Scanning files")
    registry.update("scan", done=3, total=0)

    assert registry.task("scan").percent is None


def test_button_shows_the_latest_task_and_opens_the_popup(qapp, registry):
    registry.start("scan", "Scanning files")
    registry.update("scan", done=95, total=100)

    button = TaskStatusButton()
    try:
        assert button.isVisibleTo(button.parentWidget() or button) is True
        assert "95%" in button._label.text()

        button._toggle_popup()
        popup = button._popup
        assert popup.isVisible() is True

        titles = [label.text() for label in popup.findChildren(QLabel)]
        assert any("Scanning files" in text for text in titles)
        bar = popup.findChildren(QProgressBar)[0]
        bar._step()  # easing timer; step once so the value settles
        assert bar._target == 95 or bar.value() == 95
    finally:
        button._popup.hide()
        button.close()
        registry.clear()


def test_button_hides_when_nothing_runs(qapp, registry):
    button = TaskStatusButton()
    try:
        assert button.isHidden() is True
        registry.start("scan", "Scanning files")
        qapp.processEvents()
        assert button.isHidden() is False
        registry.finish("scan")
        qapp.processEvents()
        # Finished tasks stay listed for a while, but the compact entry hides.
        assert button.isHidden() is True
    finally:
        button.close()
        registry.clear()


def test_refresh_updates_in_place_instead_of_stacking_rows(qapp, registry):
    """Every progress tick used to append another copy of the same task."""

    registry.start("scan", "Scanning files")
    popup = TaskPopup(None)
    try:
        for done in (200, 400, 600, 800, 1000):
            registry.update("scan", done=done, total=1000)
            popup.refresh()
            qapp.processEvents()
        rows = [child for child in popup.findChildren(TaskRow)]
        assert len(rows) == 1, len(rows)
        assert len(popup._rows) == 1
        assert popup._rows["scan"]._state.text().endswith("%")
    finally:
        popup.close()
        registry.clear()


def test_smooth_bar_walks_towards_its_target(qapp):
    bar = SmoothProgressBar()
    bar.set_target(60)
    assert bar.value() < 60  # not applied instantly
    for _ in range(40):
        bar._step()
    assert bar.value() == 60
