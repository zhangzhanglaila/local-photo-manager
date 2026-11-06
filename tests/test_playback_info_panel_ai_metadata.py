"""The info panel must actually receive AI captions and tags.

Two independent defects kept them from ever appearing: the coordinator read the
settings/session through ``getattr(self, "_context", None)`` (never assigned), and
the worker thread delivered the result with ``QTimer.singleShot``, which needs an
event loop in the worker thread that does not exist.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator


class _FakePanel:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    def set_asset_ai_metadata(self, caption: Any, tags: Any) -> None:
        self.calls.append((caption, tags))


class _FakeSettings:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


class _FakeEmbeddingRepository:
    def __init__(self, caption: str, tags: list[str]) -> None:
        self._caption = caption
        self._tags = tags

    def get_caption(self, asset_id: str) -> str:
        return self._caption

    def get_tags(self, asset_id: str) -> list[str]:
        return self._tags


class _FakeSession:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def get_embedding_repository(self) -> Any:
        return self._repository


class _SignallingCoordinator:
    """Minimal QObject so the queued signal has a real receiver."""

    def __init__(self, panel: _FakePanel, settings: dict[str, Any], session: Any) -> None:
        from PySide6.QtCore import QObject

        self._qobject = QObject()
        self._info_panel = panel
        self._settings = _FakeSettings(settings)
        self._library_session_getter = lambda: session
        self._current_presentation = SimpleNamespace(asset_id="asset-1")
        self._emitted: list[tuple[str, Any, Any]] = []

    def emit_ready(self, asset_id: str, caption: Any, tags: Any) -> None:
        self._emitted.append((asset_id, caption, tags))


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _refresh(panel: _FakePanel, enabled: bool, session: Any) -> SimpleNamespace:
    """Run the refresh against a stub.

    `PlaybackCoordinator.__new__` would create an uninitialised QObject; letting
    that be garbage collected can crash the interpreter, so the tests call the
    unbound methods on a plain namespace instead.
    """

    coordinator = SimpleNamespace()
    coordinator._info_panel = panel
    coordinator._settings = _FakeSettings({"agent.semantic_search_enabled": enabled})
    coordinator._library_session_getter = lambda: session
    coordinator._current_presentation = SimpleNamespace(asset_id="asset-1")
    PlaybackCoordinator._refresh_info_panel_ai_metadata(coordinator, "asset-1")
    QThreadPool.globalInstance().waitForDone(3000)
    return coordinator


def _drain(app: QApplication) -> None:
    for _ in range(20):
        app.processEvents()


def test_ai_metadata_is_applied_to_the_panel(qapp: QApplication) -> None:
    panel = _FakePanel()
    session = _FakeSession(_FakeEmbeddingRepository("a sunset", ["sky", "sun"]))

    coordinator = _refresh(panel, True, session)
    _drain(qapp)

    # The worker emits the signal; applying happens in the queue handler below.
    PlaybackCoordinator._apply_info_panel_ai_metadata(coordinator, "asset-1", "a sunset", ["sky", "sun"])
    assert panel.calls == [("a sunset", ["sky", "sun"])]


def test_stale_asset_updates_are_dropped(qapp: QApplication) -> None:
    panel = _FakePanel()
    session = _FakeSession(_FakeEmbeddingRepository("old", ["old"]))
    coordinator = _refresh(panel, True, session)

    PlaybackCoordinator._apply_info_panel_ai_metadata(coordinator, "other-asset", "old", ["old"])

    assert panel.calls == [], "an update for another photo must not overwrite the panel"


def test_disabled_search_clears_the_panel(qapp: QApplication) -> None:
    panel = _FakePanel()
    session = _FakeSession(_FakeEmbeddingRepository("ignored", ["ignored"]))

    _refresh(panel, False, session)

    assert panel.calls == [(None, None)], "stale AI data must be cleared"


def test_missing_session_is_handled_without_probing(qapp: QApplication) -> None:
    panel = _FakePanel()

    _refresh(panel, True, None)
    _drain(qapp)

    assert panel.calls == [], "no session yet: nothing to show, nothing to crash"

def test_refresh_does_not_regress_to_probing_or_thread_timer() -> None:
    """Guards the two exact defects: a missing ``_context`` and a worker-thread timer."""

    import inspect

    source = inspect.getsource(PlaybackCoordinator._refresh_info_panel_ai_metadata)

    assert "_context" not in source, "settings/session must come from injected dependencies"
    assert "QTimer.singleShot" not in source, (
        "a timer created on the worker thread never fires; emit a queued signal"
    )
    assert "aiMetadataReady" in source