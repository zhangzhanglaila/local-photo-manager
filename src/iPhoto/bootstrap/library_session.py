"""Library-scoped runtime session for vNext application boundaries."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..application.ports import (
    AssetRepositoryPort,
    AssetStateServicePort,
    EditServicePort,
    LibraryStateRepositoryPort,
    LocationAssetServicePort,
    MapInteractionServicePort,
    MapRuntimePort,
)
from ..application.services.map_interaction_service import LibraryMapInteractionService
from ..infrastructure.repositories.library_state_repository import (
    IndexStoreLibraryStateRepository,
)
from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
from ..infrastructure.services.location_metadata_service import (
    ExifToolLocationMetadataService,
)
from ..infrastructure.services.map_runtime_service import SessionMapRuntimeService
from ..application.services.assign_location_service import AssignLocationService
from ..people.service import PeopleService
from .library_asset_state_service import LibraryAssetStateService
from .library_album_metadata_service import LibraryAlbumMetadataService
from .library_asset_lifecycle_service import LibraryAssetLifecycleService
from .library_asset_operation_service import LibraryAssetOperationService
from .library_asset_query_service import LibraryAssetQueryService
from .library_edit_service import LibraryEditService
from .library_location_service import LibraryLocationService
from .library_people_service import create_people_service
from .library_scan_service import LibraryScanService

logger = logging.getLogger(__name__)


@dataclass
class LibrarySession:
    """Own library-scoped adapters and expose the application-facing surface."""

    library_root: Path
    asset_runtime: LibraryAssetRuntime | None = None
    state_repository: LibraryStateRepositoryPort | None = None
    asset_state: AssetStateServicePort | None = None
    album_metadata: LibraryAlbumMetadataService | None = None
    asset_queries: LibraryAssetQueryService | None = None
    scans: LibraryScanService | None = None
    asset_lifecycle: LibraryAssetLifecycleService | None = None
    asset_operations: LibraryAssetOperationService | None = None
    people: PeopleService | None = None
    maps: MapRuntimePort | None = None
    map_interactions: MapInteractionServicePort | None = None
    edit: EditServicePort | None = None
    locations: LocationAssetServicePort | None = None
    search_index_status: object | None = None
    bind_asset_runtime: bool = True
    # Agent services (optional, initialized lazily)
    _search_service: object = field(default=None, repr=False)
    _embedding_service: object = field(default=None, repr=False)
    _embedding_repository: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.library_root = Path(self.library_root)
        if self.asset_runtime is None:
            self.asset_runtime = LibraryAssetRuntime(self.library_root)
            self.bind_asset_runtime = False
        if self.bind_asset_runtime:
            self.asset_runtime.bind_library_root(self.library_root)
        if self.state_repository is None:
            self.state_repository = IndexStoreLibraryStateRepository(self.library_root)
        if self.asset_queries is None:
            self.asset_queries = LibraryAssetQueryService(self.library_root)
        if self.asset_state is None:
            self.asset_state = LibraryAssetStateService(
                self.library_root,
                state_repository=self.state_repository,
                favorite_query=self.asset_queries,
            )
        if self.album_metadata is None:
            self.album_metadata = LibraryAlbumMetadataService(
                self.library_root,
                state_repository=self.state_repository,
            )
        if self.scans is None:
            self.scans = LibraryScanService(self.library_root)
        if self.asset_lifecycle is None:
            self.asset_lifecycle = LibraryAssetLifecycleService(
                self.library_root,
                scan_service=self.scans,
            )
        if self.asset_operations is None:
            self.asset_operations = LibraryAssetOperationService(
                self.library_root,
                lifecycle_service=self.asset_lifecycle,
            )
        if self.people is None:
            self.people = create_people_service(self.library_root)
        if self.maps is None:
            self.maps = SessionMapRuntimeService()
        if self.map_interactions is None:
            self.map_interactions = LibraryMapInteractionService()
        if self.edit is None:
            self.edit = LibraryEditService(self.library_root)
        if self.locations is None:
            self.locations = LibraryLocationService(
                self.library_root,
                query_service=self.asset_queries,
            )
        bind_edit_service = getattr(self.asset_runtime, "bind_edit_service", None)
        if callable(bind_edit_service):
            bind_edit_service(self.edit)

    def get_search_service(self):
        """Get or initialize the search service.

        Returns
        -------
        SearchService or None
            The search service, or None if CLIP model is not available.
        """
        if self._search_service is not None:
            return self._search_service

        try:
            from ..agent.services.search_service import SearchService

            # Get embedding service and repository
            embedding_service = self.get_embedding_service()
            embedding_repo = self.get_embedding_repository()

            if embedding_service is None or embedding_repo is None:
                return None

            # Note: Don't check is_loaded() here - model loads lazily on first use
            # The search will trigger model loading when needed

            # Create search service with CLIP
            self._search_service = SearchService(
                embedding_service=embedding_service,
                asset_repository=self.asset_runtime.assets,
                embedding_repository=embedding_repo,
            )

            return self._search_service

        except Exception as e:
            logger.warning("Failed to initialize search service: %s", e)
            return None

    def reset_embedding_service(self) -> None:
        """Forget the loaded encoder so the next call loads the active model."""

        self._embedding_service = None

    def get_embedding_repository(self):
        """Get or initialize the embedding repository."""
        if self._embedding_repository is not None:
            return self._embedding_repository

        try:
            from ..cache.index_store.embedding_repository import get_embedding_repository
            self._embedding_repository = get_embedding_repository(self.library_root)
            return self._embedding_repository
        except Exception as e:
            logger.warning("Failed to initialize embedding repository: %s", e)
            return None

    def get_embedding_service(self):
        """Get or initialize the embedding service."""
        if self._embedding_service is not None:
            return self._embedding_service

        try:
            from ..agent.infrastructure.clip_embedding import CLIPEmbeddingService
            from ..agent.services import search_models

            spec = search_models.active_model()
            model_dir = search_models.model_dir(spec)
            logger.info("Checking search model %s at: %s", spec.key, model_dir)

            if not search_models.is_installed(spec):
                logger.info("Search model %s not installed at %s", spec.key, model_dir)
                self._show_model_download_dialog(spec)
                return None

            logger.info("Loading %s model from: %s", spec.family, model_dir)
            self._embedding_service = CLIPEmbeddingService(
                model_dir=model_dir,
                family=spec.family,
                status=self.search_index_status,
            )
            return self._embedding_service
        except Exception as e:
            logger.warning("Failed to initialize embedding service: %s", e)
            return None

    def _show_model_download_dialog(self, spec) -> None:
        """Offer to fetch a missing search model, with a manual fallback."""

        from ..agent.infrastructure.search_model_download import model_page_urls
        from ..agent.services import search_models
        from ..gui.ui.widgets import dialogs
        from ..i18n import tr

        target = search_models.model_dir(spec)
        choice = dialogs.ask_choice(
            None,
            tr(
                "search.model.download_prompt",
                name=spec.dir_name,
                mb=spec.download_mb,
                path=str(target),
            ),
            title=tr("search.model.title"),
            choices=[
                (tr("dialog.cancel"), "cancel"),
                (tr("search.model.download"), "manual"),
                (tr("search.model.download_and_use"), "auto"),
            ],
        )

        if choice == "auto":
            self._download_search_model(spec)
        elif choice == "manual":
            links = ", ".join(url for _label, url in model_page_urls(spec))
            dialogs.show_information(
                None,
                tr("search.model.manual_body", links=links, path=str(target)),
                title=tr("search.model.download"),
            )

    def _download_search_model(self, spec) -> None:
        """Download *spec* in the background, reporting byte progress."""

        from PySide6.QtCore import QThread, Signal

        from ..agent.infrastructure.search_model_download import download_search_model
        from ..gui.ui.widgets.progress_dialog import ProgressDialog
        from ..i18n import tr

        class _DownloadThread(QThread):
            progress = Signal(object, str)  # fraction | None, message
            done = Signal(bool, str)  # succeeded, error

            def run(self) -> None:
                try:
                    download_search_model(
                        spec,
                        progress=lambda fraction, message: self.progress.emit(
                            fraction, message
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - reported to the user
                    self.done.emit(False, str(exc))
                else:
                    self.done.emit(True, "")

        progress = ProgressDialog(
            None,
            title=tr("search.model.download"),
            message=tr("search.model.downloading", name=spec.dir_name, mb=spec.download_mb),
            dismiss_label=tr("search.model.download"),
        )
        progress.set_progress(0, 100)
        thread = _DownloadThread(self)
        self._download_thread = thread

        def _on_progress(fraction, message: str) -> None:
            percent = 0 if fraction is None else int(fraction * 100)
            progress.set_progress(percent, 100)
            if message:
                progress.set_message(message)

        thread.progress.connect(_on_progress)
        thread.done.connect(
            lambda ok, error: self._on_download_finished(ok, error, progress, spec)
        )
        progress.show()
        thread.start()

    def _on_download_finished(self, success: bool, error: str, progress, spec) -> None:
        """Close the progress card and tell the user how it went."""

        from ..agent.services import search_models
        from ..gui.ui.widgets import dialogs
        from ..i18n import tr

        progress.close()
        if success:
            dialogs.show_information(
                None,
                tr("search.model.download_done", name=spec.dir_name),
                title=tr("search.model.download"),
            )
            return
        dialogs.show_information(
            None,
            tr(
                "search.model.download_failed_hint",
                error=error,
                path=str(search_models.model_dir(spec)),
            ),
            title=tr("search.model.download_failed"),
        )

    @property
    def assets(self) -> AssetRepositoryPort:
        return self.asset_runtime.assets

    @property
    def thumbnails(self):
        return self.asset_runtime.thumbnail_service

    @property
    def state(self) -> LibraryStateRepositoryPort:
        assert self.state_repository is not None
        return self.state_repository

    def assign_location_service(self) -> AssignLocationService:
        return AssignLocationService(self.state, ExifToolLocationMetadataService())

    def shutdown(self) -> None:
        bind_edit_service = getattr(self.asset_runtime, "bind_edit_service", None)
        if callable(bind_edit_service):
            bind_edit_service(None)
        self.asset_runtime.shutdown()


def create_headless_library_session(root: Path) -> LibrarySession:
    """Create a library session for non-GUI entry points such as the CLI."""

    library_root = Path(root)
    return LibrarySession(
        library_root,
        asset_runtime=LibraryAssetRuntime(library_root),
        bind_asset_runtime=False,
        # A headless session has no map surface, so it must not load the native
        # widget DLL. That load is fatal rather than catchable when the DLL's
        # bundled Qt runtime conflicts with the one already loaded by a host
        # process that happens to own a QApplication (a test session, for
        # example), and it would take the whole process down.
        maps=SessionMapRuntimeService(allow_native_widget_probe=False),
    )


def create_library_state_repository(root: Path) -> LibraryStateRepositoryPort:
    """Create the current state adapter for compatibility entry points."""

    return IndexStoreLibraryStateRepository(Path(root))


__all__ = [
    "LibrarySession",
    "create_headless_library_session",
    "create_library_state_repository",
]
