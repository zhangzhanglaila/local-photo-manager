"""The search worker must produce results, not fail on its own state.

``SearchThread`` is a nested class inside ``_handle_search_requested``. Its ``self``
is the thread, but it called ``self._active_search_spec()`` — a coordinator method
— so every search with a bound session ended in ``AttributeError`` and the panel
kept loading. This test drives the real method with fakes and runs the worker body
synchronously.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6.QtCore", reason="the search worker is a QThread")

from iPhoto.gui.coordinators import main_coordinator as module  # noqa: E402
from iPhoto.gui.coordinators.main_coordinator import MainCoordinator  # noqa: E402


class _Sidebar:
    def __init__(self) -> None:
        self.search_states: list[tuple] = []

    def set_search_state(self, query, **kwargs) -> None:
        self.search_states.append((query, kwargs))


class _GalleryPage:
    def __init__(self) -> None:
        self.loading: list[str] = []

    def show_search_loading(self, query) -> None:
        self.loading.append(query)

    def show_model_loading(self) -> None:
        self.loading.append("<model>")

    def show_grid(self) -> None:
        pass

    def hide_loading(self) -> None:
        pass

    def show_search_results_header(self, query, count) -> None:
        pass

    def show_search_empty(self, query) -> None:
        pass


class _EmbeddingService:
    def is_loaded(self) -> bool:
        return True

    def load(self) -> None:
        pass

    def encode_text(self, text: str):
        return np.array([1.0, 0.0, 0.0], dtype=np.float32)

    def encode_text_cached(self, text: str):
        return np.array([0.0, 1.0, 0.0], dtype=np.float32)


class _Session:
    def get_embedding_service(self):
        return _EmbeddingService()

class _Settings:
    """Minimal settings store: the search path records history and reads precision."""

    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def get(self, key: str, default=None):
        return self._values.get(key, default)

    def set(self, key: str, value) -> None:
        self._values[key] = value

class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_message(self, message, timeout_ms=None) -> None:
        self.messages.append(message)


class _Coordinator(MainCoordinator):
    """MainCoordinator with the search surface stubbed out."""

    def __init__(self) -> None:  # noqa: D107 - deliberately skips QObject.__init__
        self._logger = logging.getLogger("test.search")
        self._search_state = None
        self._search_serial = 0
        self._search_threads: list = []
        self._search_thread = None
        self._window = SimpleNamespace(
            ui=SimpleNamespace(sidebar=_Sidebar(), gallery_page=_GalleryPage())
        )
        self._view_router = SimpleNamespace(show_gallery=lambda: None)
        self._context = SimpleNamespace(
            library=SimpleNamespace(root=lambda: Path("D:/library")),
            settings=_Settings(),
        )
        self._status_bar = _StatusBar()
        self.spec_lookups = 0

    # Stubs for the surface the method touches.
    def _show_search_loading(self, query, sub_message=None, show_continue=False) -> None:
        pass

    def _active_search_spec(self):
        self.spec_lookups += 1
        return SimpleNamespace(dir_name="siglip2-base", family="siglip")

    def _active_session(self):
        return _Session()

    def _search_precision(self) -> str:
        return "balanced"

    def _current_album_rel_path(self):
        return None

    def _retire_search_thread(self, thread) -> None:
        pass

    def _is_search_page_visible(self) -> bool:
        return False


def _install_fakes(monkeypatch) -> None:
    from iPhoto.agent.services import search_models
    from iPhoto.bootstrap import embedding_index

    monkeypatch.setattr(search_models, "is_installed", lambda spec, **kw: True)
    monkeypatch.setattr(search_models, "model_dir", lambda spec: Path("D:/models"))
    monkeypatch.setattr(
        embedding_index,
        "load_stored_embeddings",
        lambda root, model_name=None: [
            {"asset_id": "asset-a", "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32)},
            {"asset_id": "asset-b", "embedding": np.array([0.0, 1.0, 0.0], dtype=np.float32)},
            {"asset_id": "asset-c", "embedding": np.array([0.5, 0.5, 0.0], dtype=np.float32)},
        ],
    )
    from iPhoto.agent.services import search_ranking

    monkeypatch.setattr(
        search_ranking,
        "select_results",
        lambda scores, **kwargs: list(range(min(2, len(scores)))),
    )
    from iPhoto.config import SearchSettings

    monkeypatch.setattr(SearchSettings, "from_env", classmethod(lambda cls: SimpleNamespace(min_similarity_override=None)))


def test_search_worker_returns_results(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    coordinator = _Coordinator()

    MainCoordinator._handle_search_requested(coordinator, "dog")

    assert coordinator._search_threads, "the worker was created"
    thread = coordinator._search_threads[-1]
    captured: list[tuple] = []
    thread.finished.connect(lambda results, error: captured.append((results, error)))

    thread.run()

    assert captured, "the worker reported back"
    results, error = captured[-1]
    assert error == ""
    assert results, "at least one match is returned"
    assert {item["asset_id"] for item in results} <= {"asset-a", "asset-b", "asset-c"}
    assert coordinator.spec_lookups >= 1
