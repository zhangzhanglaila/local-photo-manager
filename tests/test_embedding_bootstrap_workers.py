"""The embedding bootstrap workers must actually reach their work.

Both nested ``QRunnable`` classes in ``MainCoordinator`` used to reference the
coordinator's state through their *own* ``self`` and read an out-of-scope
``force_rebuild``: the silent path swallowed the error in a debug log, and the
interactive path raised ``NameError`` after the model had already loaded. This
test runs the worker body with fakes and checks that an ``EmbeddingWorker`` is
built with the coordinator's model spec.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtCore", reason="the coordinator is a Qt object")

from iPhoto.gui.coordinators import main_coordinator as module  # noqa: E402
from iPhoto.gui.coordinators.main_coordinator import MainCoordinator  # noqa: E402


class _Assets:
    def read_all(self):
        return [{"id": "asset-a", "media_type": 0}]


class _Repository:
    def __init__(self) -> None:
        self.model_names: list[str | None] = []

    def get_asset_ids_without_embeddings(self, asset_ids, model_name=None):
        self.model_names.append(model_name)
        return ["asset-a"]

    def count(self) -> int:
        return 1


class _Service:
    def is_loaded(self) -> bool:
        return True


class _Session:
    def __init__(self) -> None:
        self.asset_runtime = SimpleNamespace(assets=_Assets())
        self.library_root = Path("D:/library")
        self.repository = _Repository()

    def get_embedding_service(self):
        return _Service()

    def get_embedding_repository(self):
        return self.repository


class _SearchIndexStatus:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_indexing(self, *args) -> None:
        self.calls.append(args)


class _BackgroundTasks:
    def __init__(self) -> None:
        self.started: list[tuple] = []

    def start(self, *args, **kwargs) -> None:
        self.started.append((args, kwargs))

    def update(self, *args, **kwargs) -> None:
        pass

    def finish(self, *args, **kwargs) -> None:
        pass


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_message(self, message, timeout_ms=None) -> None:
        self.messages.append(message)


class _Coordinator(MainCoordinator):
    """``MainCoordinator`` without a window, context wiring, or Qt parents."""

    def __init__(self) -> None:  # noqa: D107 - deliberately not QObject.__init__
        self._context = SimpleNamespace(
            library_session=_Session(),
            search_index_status=_SearchIndexStatus(),
            background_tasks=_BackgroundTasks(),
        )
        self._status_bar = _StatusBar()
        self._logger = logging.getLogger(__name__)
        self.statuses: list[tuple] = []
        self.spec_lookups = 0

    def _active_search_spec(self):
        self.spec_lookups += 1
        return SimpleNamespace(dir_name="siglip2-base")

    def _handle_embedding_status(self, *args) -> None:
        self.statuses.append(args)


class _Signals:
    def __init__(self) -> None:
        self.progress = SimpleNamespace(connect=lambda *_a, **_k: None)
        self.finished = SimpleNamespace(connect=lambda *_a, **_k: None)
        self.error = SimpleNamespace(connect=lambda *_a, **_k: None)


class _FakeEmbeddingWorker:
    instances: list["_FakeEmbeddingWorker"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.signals = _Signals()
        _FakeEmbeddingWorker.instances.append(self)


def _install_fakes(monkeypatch) -> list:
    started: list = []

    class _Pool:
        def start(self, worker) -> None:
            started.append(worker)

    monkeypatch.setattr(
        module,
        "QThreadPool",
        SimpleNamespace(globalInstance=lambda: _Pool()),
    )
    from iPhoto.agent.services import search_models
    from iPhoto.gui.ui.tasks import embedding_worker

    monkeypatch.setattr(search_models, "is_installed", lambda spec, **kw: True)
    monkeypatch.setattr(search_models, "model_dir", lambda spec: Path("D:/models"))

    _FakeEmbeddingWorker.instances = []
    monkeypatch.setattr(embedding_worker, "EmbeddingWorker", _FakeEmbeddingWorker)
    return started


def test_silent_bootstrap_builds_a_worker_with_the_coordinator_spec(monkeypatch) -> None:
    started = _install_fakes(monkeypatch)
    coordinator = _Coordinator()

    MainCoordinator._start_embedding_generation_silent(coordinator, force_rebuild=True)

    assert len(started) == 1, "the silent worker was queued"
    started[0].run()

    assert len(_FakeEmbeddingWorker.instances) == 1
    worker = _FakeEmbeddingWorker.instances[0]
    assert worker.kwargs["force_rebuild"] is True
    assert coordinator.spec_lookups >= 1
    session = coordinator._context.library_session
    assert session.repository.model_names == ["siglip2-base"]


def test_interactive_bootstrap_builds_a_worker_with_the_coordinator_spec(
    monkeypatch,
) -> None:
    started = _install_fakes(monkeypatch)
    coordinator = _Coordinator()
    coordinator._window = SimpleNamespace(
        ui=SimpleNamespace(
            gallery_page=SimpleNamespace(show_loading_message=lambda *_a, **_k: None)
        )
    )

    MainCoordinator._start_embedding_generation(coordinator)

    assert len(started) == 1
    started[0].run()

    assert len(_FakeEmbeddingWorker.instances) == 1
    worker = _FakeEmbeddingWorker.instances[0]
    assert worker.kwargs.get("force_rebuild", False) is False
    assert coordinator.spec_lookups >= 1
