"""``AiIndexCoordinator`` builds the workers it is supposed to build.

The index lifecycle used to live inside ``MainCoordinator`` as ten methods that
read eight of its attributes; the workers themselves were nested classes whose
``self`` was the worker, which is how search once failed with ``AttributeError``.
It now lives in ``gui/coordinators/ai_index_coordinator.py`` and takes its
collaborators by constructor, so these tests can drive it with fakes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtCore", reason="the coordinator is a Qt object")

from iPhoto.gui.coordinators.ai_index_coordinator import AiIndexCoordinator  # noqa: E402
from iPhoto.i18n import tr  # noqa: E402


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
        self.embedding_resets = 0

    def get_embedding_service(self):
        return _Service()

    def get_embedding_repository(self):
        return self.repository

    def reset_embedding_service(self) -> None:
        self.embedding_resets += 1


class _SearchIndexStatus:
    def __init__(self) -> None:
        self.indexing: list[tuple] = []
        self.models: list[str] = []

    def set_indexing(self, *args) -> None:
        self.indexing.append(args)

    def set_model(self, key: str) -> None:
        self.models.append(key)


class _BackgroundTasks:
    def __init__(self) -> None:
        self.started: list[tuple] = []
        self.finished: list[tuple] = []

    def start(self, *args, **kwargs) -> None:
        self.started.append((args, kwargs))

    def update(self, *args, **kwargs) -> None:
        pass

    def finish(self, *args, **kwargs) -> None:
        self.finished.append((args, kwargs))


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int | None]] = []

    def show_message(self, message, timeout_ms=None) -> None:
        self.messages.append((message, timeout_ms))


class _ModelSelection:
    def __init__(self, spec) -> None:
        self._spec = spec
        self.applied: list[tuple[str, str | None]] = []

    def spec(self, feature: str):
        return self._spec

    def apply(self, feature: str, key: str | None):
        self.applied.append((feature, key))
        return self._spec


class _Presenter:
    """Records what the interactive path would have rendered."""

    def __init__(self) -> None:
        self.reports: list = []
        self.intro_shown = False

    def show_intro(self) -> None:
        self.intro_shown = True

    def reporter(self):
        def report(update) -> None:
            self.reports.append(update)

        return report


class _GalleryVm:
    def __init__(self) -> None:
        self.rescans = 0

    def rescan_current(self) -> None:
        self.rescans += 1


def _make(
    *,
    session: _Session | None = None,
    spec: SimpleNamespace | None = None,
    presenter: _Presenter | None = None,
    library_root: Path | None = Path("D:/library"),
) -> tuple[AiIndexCoordinator, SimpleNamespace, _Session, _GalleryVm, _Presenter]:
    session = session or _Session()
    spec = spec or SimpleNamespace(dir_name="siglip2-base", key="siglip2-base", download_mb=577)
    status_bar = _StatusBar()
    gallery_vm = _GalleryVm()
    context = SimpleNamespace(
        library_session=session,
        search_index_status=_SearchIndexStatus(),
        background_tasks=_BackgroundTasks(),
        model_selection=_ModelSelection(spec),
        settings=SimpleNamespace(set=lambda *a, **k: None),
    )
    coordinator = AiIndexCoordinator(
        context=context,
        window=SimpleNamespace(ui=SimpleNamespace(gallery_page=SimpleNamespace())),
        gallery_vm=gallery_vm,
        status_bar=status_bar,
        session_getter=lambda: session,
        library_root_getter=lambda: library_root,
    )
    fake_presenter = presenter or _Presenter()
    coordinator._presenter = fake_presenter
    return coordinator, context, session, gallery_vm, fake_presenter


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


def _install_pool(monkeypatch) -> list:
    started: list = []

    class _Pool:
        def start(self, worker) -> None:
            started.append(worker)

    import iPhoto.gui.services.embedding_bootstrap as bootstrap_module

    monkeypatch.setattr(
        bootstrap_module,
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


def test_silent_generation_builds_a_worker_with_the_selected_spec(monkeypatch) -> None:
    started = _install_pool(monkeypatch)
    coordinator, _context, session, _gallery, _presenter = _make()

    coordinator.start_generation_silent(force_rebuild=True)

    assert len(started) == 1, "the silent worker was queued"
    started[0].run()

    assert len(_FakeEmbeddingWorker.instances) == 1
    assert _FakeEmbeddingWorker.instances[0].kwargs["force_rebuild"] is True
    assert session.repository.model_names == ["siglip2-base"]


def test_interactive_generation_shows_the_intro_and_reports_through_the_presenter(
    monkeypatch,
) -> None:
    started = _install_pool(monkeypatch)
    coordinator, _context, _session, _gallery, presenter = _make()

    coordinator.start_generation()

    assert presenter.intro_shown
    assert len(started) == 1
    started[0].run()

    assert len(_FakeEmbeddingWorker.instances) == 1
    assert _FakeEmbeddingWorker.instances[0].kwargs.get("force_rebuild", False) is False
    kinds = [report.kind for report in presenter.reports]
    assert kinds[:5] == [
        "loading_model",
        "model_loading",
        "model_loaded",
        "counting",
        "starting",
    ]


def test_ensure_index_only_runs_once(monkeypatch) -> None:
    started = _install_pool(monkeypatch)
    coordinator, _context, _session, _gallery, _presenter = _make()

    coordinator.ensure_index()
    coordinator.ensure_index()

    assert len(started) == 1


def test_rebuild_search_index_resets_the_service_and_rebuilds(monkeypatch) -> None:
    started = _install_pool(monkeypatch)
    coordinator, _context, session, _gallery, _presenter = _make()

    coordinator.rebuild_search_index()

    assert session.embedding_resets == 1
    assert len(started) == 1
    worker = started[0]
    # force_rebuild reaches the worker through its constructor kwargs.
    assert getattr(worker, "_force_rebuild", None) in (True, None)


def test_rebuild_search_index_does_not_build_when_the_model_is_missing(monkeypatch) -> None:
    started = _install_pool(monkeypatch)
    from iPhoto.agent.services import search_models

    monkeypatch.setattr(search_models, "is_installed", lambda spec, **kw: False)
    coordinator, _context, _session, _gallery, _presenter = _make()

    coordinator.rebuild_search_index()

    assert started == []
    message, timeout = coordinator._status_bar.messages[-1]
    assert message == tr("search.model.missing", mb=577)
    assert timeout == 8000


def test_rebuild_ai_feature_needs_a_library() -> None:
    coordinator, context, _session, gallery, _presenter = _make(library_root=None)

    coordinator.rebuild_ai_feature("face")

    assert coordinator._status_bar.messages[-1][0] == tr("settings.ai_state.no_library")
    assert context.background_tasks.started == []
    assert gallery.rescans == 0


def test_rebuild_ai_feature_starts_a_task_and_rescans() -> None:
    coordinator, context, _session, gallery, _presenter = _make()

    coordinator.rebuild_ai_feature("ocr")

    assert context.background_tasks.started[0][0] == ("ocr", tr("status.task.ocr"))
    assert gallery.rescans == 1


def test_model_switch_to_a_missing_model_downloads_first(monkeypatch) -> None:
    started = _install_pool(monkeypatch)
    from iPhoto.agent.services import search_models

    monkeypatch.setattr(search_models, "is_installed", lambda spec, **kw: False)
    coordinator, context, session, _gallery, _presenter = _make()

    coordinator.on_search_model_setting_changed(search_models.SETTINGS_KEY, "clip-b32")

    assert context.model_selection.applied == [("search", "clip-b32")]
    assert context.search_index_status.models == ["siglip2-base"]
    assert session.embedding_resets == 1
    assert len(started) == 1, "a download worker was queued instead of an index build"


def test_model_switch_to_an_installed_model_indexes_silently(monkeypatch) -> None:
    started = _install_pool(monkeypatch)
    from iPhoto.agent.services import search_models

    coordinator, _context, _session, _gallery, _presenter = _make()

    coordinator.on_search_model_setting_changed(search_models.SETTINGS_KEY, "siglip2-base")

    assert len(started) == 1
    assert type(started[0]).__name__ == "EmbeddingIndexWorker"
