"""The semantic-search model is warmed before the face scan starts.

It used to load lazily on the first search: the user waited ~10 s in front of a
"loading AI model (350MB)" panel. Loading it as soon as the file scan finishes means
search is ready by the time the (CPU heavy) face scan is done.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.library.runtime_controller import LibraryRuntimeController


class _InlineThread:
    """Run the target synchronously: no real thread outlives the test process."""

    def __init__(self, target=None, name=None, daemon=None) -> None:
        self._target = target
        self.name = name or ""

    def start(self) -> None:
        if self._target is not None:
            self._target()

    def join(self, timeout=None) -> None:  # pragma: no cover - nothing to wait for
        return None


@pytest.fixture(autouse=True)
def _inline_threads(monkeypatch: pytest.MonkeyPatch):
    import threading

    monkeypatch.setattr(threading, "Thread", _InlineThread)


@pytest.fixture(autouse=True)
def _model_available(monkeypatch: pytest.MonkeyPatch):
    """Pretend the CLIP model is downloaded so the preload proceeds."""

    import iPhoto.agent.infrastructure.clip_downloader as downloader

    monkeypatch.setattr(downloader, "is_model_available", lambda *_a, **_k: True)
    monkeypatch.setattr(downloader, "get_model_dir", lambda root: Path(str(root)))


class _Service:
    def __init__(self) -> None:
        self.calls = 0

    def load(self) -> bool:
        self.calls += 1
        return True

    def is_loaded(self) -> bool:  # pragma: no cover - must not be the trigger
        return False


class _Session:
    def __init__(self) -> None:
        self.service = _Service()
        self.library_root = Path("/library")

    def get_embedding_service(self) -> _Service:
        return self.service


def test_preload_starts_once_and_loads_the_model() -> None:
    controller = LibraryRuntimeController.__new__(LibraryRuntimeController)
    controller._search_model_preload_started = False
    session = _Session()
    controller._library_session = session

    LibraryRuntimeController._preload_search_model(controller)
    LibraryRuntimeController._preload_search_model(controller)

    assert controller._search_model_preload_started is True
    assert session.service.calls == 1, "the model is loaded once, not on every scan"


def test_preload_is_skipped_without_a_session() -> None:
    controller = LibraryRuntimeController.__new__(LibraryRuntimeController)
    controller._search_model_preload_started = False
    controller._library_session = None

    LibraryRuntimeController._preload_search_model(controller)

    assert controller._search_model_preload_started is True


def test_missing_embedding_service_is_tolerated() -> None:
    controller = LibraryRuntimeController.__new__(LibraryRuntimeController)
    controller._search_model_preload_started = False
    controller._library_session = SimpleNamespace(get_embedding_service=lambda: None)

    LibraryRuntimeController._preload_search_model(controller)
