"""Own the AI-index lifecycle: model download, index builds, model switching.

These ten methods used to live in ``MainCoordinator``, where they read eight of
its attributes (context, window, gallery view model, status bar, logger, the
active search spec, the status presenter, the open session). Nothing here is on
the gallery hot path: it runs when a model has to be fetched, when the semantic
index is (re)built, or when the user switches models.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from iPhoto.i18n import tr
from iPhoto.gui.services import embedding_bootstrap
from iPhoto.gui.services.ai_index_status import AiIndexStatusPresenter
from iPhoto.gui.services.embedding_bootstrap import (
    EmbeddingIndexWorker,
    EmbeddingInitWorker,
    SearchModelDownloadWorker,
)
from iPhoto.gui.services.search_thread import clear_search_embedding_cache

_LOGGER = logging.getLogger(__name__)


class AiIndexCoordinator(QObject):
    """Drive model downloads and index builds, and report them to the user."""

    def __init__(
        self,
        *,
        context,
        window,
        gallery_vm,
        status_bar,
        session_getter: Callable[[], object | None],
        library_root_getter: Callable[[], object | None],
    ) -> None:
        super().__init__()
        self._context = context
        self._window = window
        self._gallery_vm = gallery_vm
        self._status_bar = status_bar
        self._session_getter = session_getter
        self._library_root_getter = library_root_getter
        self._presenter: AiIndexStatusPresenter | None = None
        self._bootstrap_started = False

    # ---------------------------------------------------------------- helpers

    def session(self):
        """The open library session, when there is one."""

        return self._session_getter()

    def search_spec(self):
        """The search model currently selected."""

        return self._context.model_selection.spec("search")

    @property
    def presenter(self) -> AiIndexStatusPresenter:
        """Progress presenter for the interactive (first-search) path."""

        if self._presenter is None:
            self._presenter = AiIndexStatusPresenter(
                self._window.ui.gallery_page, self._status_bar
            )
        return self._presenter

    def show_status(self, message: str, timeout_ms: int = 5000) -> None:
        """Show *message* in the status bar, marshalled onto the GUI thread."""

        QTimer.singleShot(
            0, lambda: self._status_bar.show_message(message, timeout_ms)
        )

    # --------------------------------------------------------------- commands

    def ensure_index(self) -> None:
        """Start silent index generation once, if it has not run yet."""

        if self._bootstrap_started:
            return
        self._bootstrap_started = True
        try:
            self.start_generation_silent()
        except Exception:
            _LOGGER.debug("Embedding bootstrap failed", exc_info=True)

    def start_generation_silent(self, *, force_rebuild: bool = False) -> None:
        """Build the index in the background, reporting only in the status bar."""

        library_session = self.session()
        if library_session is None:
            return

        worker = EmbeddingIndexWorker(
            library_session,
            model_spec=self.search_spec(),
            status=self._context.search_index_status,
            tasks=self._context.background_tasks,
            status_callback=self.show_status,
            force_rebuild=force_rebuild,
        )
        embedding_bootstrap.QThreadPool.globalInstance().start(worker)

    def start_generation(self) -> None:
        """Build the index with the full progress panel (first search)."""

        library_session = self.session()
        if library_session is None:
            return

        presenter = self.presenter
        presenter.show_intro()

        worker = EmbeddingInitWorker(
            library_session,
            model_spec=self.search_spec(),
            callback=presenter.reporter(),
        )
        embedding_bootstrap.QThreadPool.globalInstance().start(worker)

    def rebuild_search_index(self) -> None:
        """Re-embed the whole library with the active model."""

        from iPhoto.agent.services import search_models

        session = self.session()
        reset = getattr(session, "reset_embedding_service", None)
        if callable(reset):
            reset()
        clear_search_embedding_cache()
        spec = self.search_spec()
        if not search_models.is_installed(spec):
            self._status_bar.show_message(
                tr("search.model.missing", mb=spec.download_mb), 8000
            )
            return
        self._status_bar.show_message(tr("search.model.rebuild_started"), 5000)
        self.start_generation_silent(force_rebuild=True)

    def rebuild_ai_feature(self, feature: str) -> None:
        """Re-run the face or text scan for the photos that still need it.

        Both workers pull the remaining ``pending`` rows as their queue drains, so
        a library rescan is enough to backfill them; nothing is thrown away.
        """

        root = self._library_root_getter()
        if root is None:
            self._status_bar.show_message(tr("settings.ai_state.no_library"), 6000)
            return
        title = (
            tr("status.task.face") if feature == "face" else tr("status.task.ocr")
        )
        tasks = self._context.background_tasks

        tasks.start(feature, title)
        self._status_bar.show_message(
            tr("settings.ai_state.scanning", title=title), 8000
        )
        try:
            self._gallery_vm.rescan_current()
        except Exception:  # pragma: no cover - surfaced in the status bar
            _LOGGER.warning(
                "could not start the %s rebuild", feature, exc_info=True
            )
            tasks.finish(feature, failed=True)

    def on_semantic_search_toggled(self, enabled: bool) -> None:
        """Persist the semantic-search switch and, when enabling, build the index."""

        self._context.settings.set("agent.semantic_search_enabled", enabled)

        if enabled:
            self.start_generation()
            self._status_bar.show_message(
                tr("agent.enabled", default="Semantic search enabled")
            )
        else:
            self._status_bar.show_message(
                tr("agent.disabled", default="Semantic search disabled")
            )

    def on_search_model_setting_changed(self, key: str, value: object) -> None:
        """React to a model change made by the wizard or the settings window."""

        from iPhoto.agent.services import ai_features, search_models

        if key == search_models.REBUILD_KEY:
            self.rebuild_search_index()
            return
        if key in (ai_features.FACE_REBUILD_KEY, ai_features.OCR_REBUILD_KEY):
            self.rebuild_ai_feature(
                "face" if key == ai_features.FACE_REBUILD_KEY else "ocr"
            )
            return
        if key != search_models.SETTINGS_KEY:
            return
        spec = self._context.model_selection.apply(
            "search", str(value) if value else None
        )
        _LOGGER.info("search model switched to %s", spec.key)

        self._context.search_index_status.set_model(spec.key)
        session = self.session()
        reset = getattr(session, "reset_embedding_service", None)
        if callable(reset):
            reset()
        clear_search_embedding_cache()
        if not search_models.is_installed(spec):
            self._status_bar.show_message(
                tr("search.model.missing", mb=spec.download_mb), 8000
            )
            self.download_model_then_index(spec)
            return
        # Vectors from the previous model stay on disk (so switching back is
        # instant) but search ignores them; fill in the new model's rows.
        self.start_generation_silent()

    def download_model_then_index(self, spec) -> None:
        """Fetch missing weights in the background, then index with them."""

        worker = SearchModelDownloadWorker(
            spec,
            tasks=self._context.background_tasks,
            status_callback=self.show_status,
            on_downloaded=self.start_generation_silent,
        )
        embedding_bootstrap.QThreadPool.globalInstance().start(worker)


__all__ = ["AiIndexCoordinator"]
