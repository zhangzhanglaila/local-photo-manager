"""Background-task registry and the status-bar popup that lists it."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QWidget

from iPhoto.gui.services.background_tasks import BackgroundTaskRegistry
from iPhoto.i18n import tr
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
def registry() -> BackgroundTaskRegistry:
    return BackgroundTaskRegistry()


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


def test_active_ai_scan_is_listed_before_a_waiting_one(registry):
    """Only one AI scan runs at a time; the blocked one must not look current."""

    registry.start("ocr", "Recognising text")
    registry.set_state("ocr", "waiting", detail="waiting…")
    registry.start("face", "Recognising faces")
    registry.update("face", done=50, total=100)

    assert [task.id for task in registry.tasks()] == ["face", "ocr"]
    assert registry.latest().id == "face"
    assert registry.task("ocr").running is True
    assert registry.task("ocr").waiting is True


def test_finished_tasks_sit_above_unfinished_ones(registry):
    """The popup grows upward, so history belongs above the moving work."""

    registry.start("scan", "Scanning files")
    registry.update("scan", done=20, total=100)
    registry.start("index", "Building the search index")
    registry.update("index", done=100, total=100)
    registry.finish("index")

    assert [task.id for task in registry.tasks()] == ["index", "scan"]
    assert registry.latest().id == "scan"


def test_popup_bottom_stays_above_the_status_bar(qapp, registry):
    """Removing a row must shrink the card upward, not leave a gap below."""

    registry.start("index", "Building the search index")
    registry.update("index", done=100, total=100)
    registry.start("scan", "Scanning files")
    registry.update("scan", done=20, total=100)

    host = QWidget()
    host.resize(640, 420)
    button = TaskStatusButton(host, registry=registry)
    button.move(420, 380)
    try:
        host.show()
        button.show()
        button._toggle_popup()
        qapp.processEvents()
        popup = button._popup
        before = popup.geometry().bottom()
        registry.finished_ttl_s = 0.0

        registry.finish("index")
        qapp.processEvents()

        assert popup.isVisible() is True
        assert popup.geometry().bottom() == before
        assert "index" not in popup._rows
    finally:
        button._popup.hide()
        button.close()
        host.close()
        registry.clear()


def test_finishing_the_active_task_promotes_the_waiting_one(registry):
    registry.start("ocr", "Recognising text")
    registry.set_state("ocr", "waiting")
    registry.start("face", "Recognising faces")
    registry.update("face", done=100, total=100)

    registry.finish("face")

    assert registry.latest().id == "ocr"
    assert registry.task("ocr").state == "waiting"


def test_button_shows_the_latest_task_and_opens_the_popup(qapp, registry):
    registry.start("scan", "Scanning files")
    registry.update("scan", done=95, total=100)

    button = TaskStatusButton(registry=registry)
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


def test_button_names_the_active_scan_even_when_another_is_waiting(qapp, registry):
    """A waiting OCR scan must not steal the label from the moving face scan."""

    registry.start("ocr", tr("status.task.ocr"))
    registry.set_state("ocr", "waiting", detail=tr("status.tasks_waiting"))
    registry.start("face", tr("status.task.face"))
    registry.update("face", done=50, total=100)

    button = TaskStatusButton(registry=registry)
    try:
        assert tr("status.task.face") in button._label.text()
        assert "50%" in button._label.text()
        assert tr("status.tasks_waiting") not in button._label.text()

        button._toggle_popup()
        assert list(button._popup._rows) == ["face", "ocr"]
        assert button._popup._rows["ocr"]._state.text() == tr("status.tasks_waiting")

        # The gate hands over: OCR starts moving and face becomes the waiter.
        # The popup is already open, so the rows must be reordered, not just
        # refreshed in place.
        registry.update("ocr", done=10, total=100)
        registry.set_state("ocr", "running")
        registry.set_state("face", "waiting", detail=tr("status.tasks_waiting"))
        button._popup.refresh()
        assert button._popup._layout.itemAt(1).widget().task_id == "ocr"
        assert button._popup._layout.itemAt(2).widget().task_id == "face"
    finally:
        button._popup.hide()
        button.close()
        registry.clear()


def test_button_hides_when_nothing_runs(qapp, registry):
    button = TaskStatusButton(registry=registry)
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
    popup = TaskPopup(None, registry)
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


def test_scan_task_names_the_folder(qapp, registry, monkeypatch):
    """With several roots, "scanning files" must say which folder it means."""

    from pathlib import Path

    from iPhoto.gui.ui.controllers.status_bar_controller import StatusBarController

    class _Bar:
        def showMessage(self, *args, **kwargs):
            pass

        def setVisible(self, *args, **kwargs):
            pass

    class _Bar2:
        def __init__(self):
            self.range = (0, 0)
            self.value = 0
            self.visible = False

        def setRange(self, low, high):
            self.range = (low, high)

        def setValue(self, value):
            self.value = value

        def setVisible(self, visible):
            self.visible = visible

    controller = StatusBarController.__new__(StatusBarController)
    controller._progress_context = "scan"
    controller._rescan_action = None
    controller._move_context_delete = False
    controller._move_context_restore = False
    controller._progress_bar = _Bar2()
    controller._status_bar = _Bar()
    controller._background_tasks = registry
    monkeypatch.setattr(controller, "show_message", lambda *a, **k: None)
    registry.start("scan", "Scanning files")

    controller.handle_scan_progress(Path(r"D:\APPLE"), 600, 980)

    task = registry.task("scan")
    assert "APPLE" in task.title
    assert "APPLE" in task.detail
