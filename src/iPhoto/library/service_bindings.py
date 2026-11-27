"""Session-scoped service bindings for :class:`LibraryRuntimeController`.

Every ``bind_*`` call replaces one entry in the controller's
:class:`~iPhoto.library.service_registry.LibraryServiceRegistry` snapshot and the
matching property reads it back. Keeping that plumbing in its own module leaves
the runtime controller responsible for roots, the album tree and watch lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from ..people.service import PeopleService

if TYPE_CHECKING:  # pragma: no cover
    from ..application.ports import (
        AssetStateServicePort,
        EditServicePort,
        LibraryStateRepositoryPort,
        LocationAssetServicePort,
        MapInteractionServicePort,
        MapRuntimePort,
    )
    from ..bootstrap.library_session import LibrarySession
    from ..infrastructure.services.library_album_metadata_service import (
        LibraryAlbumMetadataService,
    )
    from ..infrastructure.services.library_asset_lifecycle_service import (
        LibraryAssetLifecycleService,
    )
    from ..infrastructure.services.library_asset_operation_service import (
        LibraryAssetOperationService,
    )
    from ..infrastructure.services.library_asset_query_service import LibraryAssetQueryService
    from ..infrastructure.services.library_scan_service import LibraryScanService


class LibraryServiceBindingsMixin:
    """Bind/unbind the session-scoped services exposed by the runtime controller."""

    def bind_library_session(
        self,
        library_session: "LibrarySession | None",
        *,
        owned: bool = False,
    ) -> None:
        """Bind or clear the active library session surface for this manager."""

        previous = self._library_session
        previous_owned = self._owns_library_session
        if previous is library_session and previous is not None:
            self._owns_library_session = owned
            return

        self._library_session = library_session
        self._owns_library_session = bool(library_session is not None and owned)

        if library_session is None:
            self.bind_location_service(None)
            self.bind_edit_service(None)
            self.bind_map_interaction_service(None)
            self.bind_map_runtime(None)
            self.bind_people_service(None)
            self.bind_asset_operation_service(None)
            self.bind_asset_lifecycle_service(None)
            self.bind_album_metadata_service(None)
            self.bind_asset_state_service(None)
            self.bind_state_repository(None)
            self.bind_asset_query_service(None)
            self.bind_scan_service(None)
        else:
            self.bind_asset_query_service(library_session.asset_queries)
            self.bind_state_repository(library_session.state)
            self.bind_asset_state_service(library_session.asset_state)
            self.bind_album_metadata_service(library_session.album_metadata)
            self.bind_location_service(library_session.locations)
            self.bind_edit_service(library_session.edit)
            self.bind_scan_service(library_session.scans)
            self.bind_asset_lifecycle_service(library_session.asset_lifecycle)
            self.bind_asset_operation_service(library_session.asset_operations)
            self.bind_people_service(library_session.people)
            self.bind_map_runtime(library_session.maps)
            self.bind_map_interaction_service(library_session.map_interactions)

        if previous is not None and previous is not library_session and previous_owned:
            previous.shutdown()

    @property
    def library_session(self) -> "LibrarySession | None":
        return self._library_session

    def bind_scan_service(self, scan_service: "LibraryScanService | None") -> None:
        """Bind the current library session scan command surface."""

        self._services = self._services.with_service("scan_service", scan_service)

    @property
    def scan_service(self) -> "LibraryScanService | None":
        return self._services.scan_service

    def bind_asset_query_service(
        self,
        asset_query_service: "LibraryAssetQueryService | None",
    ) -> None:
        """Bind the current library session asset query surface."""

        self._services = self._services.with_service("asset_query_service", asset_query_service)
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        active_session = self._library_session
        default_location_service = (
            active_session.locations if active_session is not None else None
        )
        current_root = self.root()
        if (
            current_root is not None
            and asset_query_service is not None
            and self._services.location_service is default_location_service
        ):
            from ..infrastructure.services.library_location_service import LibraryLocationService

            self._services = self._services.with_service(
                "location_service",
                LibraryLocationService(
                    current_root,
                    query_service=asset_query_service,
                ),
            )

    @property
    def asset_query_service(self) -> "LibraryAssetQueryService | None":
        return self._services.asset_query_service

    def bind_state_repository(
        self,
        state_repository: "LibraryStateRepositoryPort | None",
    ) -> None:
        """Bind the current library session durable-state surface."""

        self._services = self._services.with_service("state_repository", state_repository)

    @property
    def state_repository(self) -> "LibraryStateRepositoryPort | None":
        return self._services.state_repository

    def bind_asset_state_service(
        self,
        asset_state_service: "AssetStateServicePort | None",
    ) -> None:
        """Bind the current library session asset-state command surface."""

        self._services = self._services.with_service("asset_state_service", asset_state_service)

    @property
    def asset_state_service(self) -> "AssetStateServicePort | None":
        return self._services.asset_state_service

    def bind_album_metadata_service(
        self,
        album_metadata_service: "LibraryAlbumMetadataService | None",
    ) -> None:
        """Bind the current library session album metadata command surface."""

        self._services = self._services.with_service("album_metadata_service", album_metadata_service)

    @property
    def album_metadata_service(self) -> "LibraryAlbumMetadataService | None":
        return self._services.album_metadata_service

    def bind_asset_lifecycle_service(
        self,
        asset_lifecycle_service: "LibraryAssetLifecycleService | None",
    ) -> None:
        """Bind the current library session asset lifecycle command surface."""

        self._services = self._services.with_service("asset_lifecycle_service", asset_lifecycle_service)

    @property
    def asset_lifecycle_service(self) -> "LibraryAssetLifecycleService | None":
        return self._services.asset_lifecycle_service

    def bind_asset_operation_service(
        self,
        asset_operation_service: "LibraryAssetOperationService | None",
    ) -> None:
        """Bind the current library session file-operation command surface."""

        self._services = self._services.with_service("asset_operation_service", asset_operation_service)

    @property
    def asset_operation_service(self) -> "LibraryAssetOperationService | None":
        return self._services.asset_operation_service

    def bind_people_service(self, people_service: PeopleService | None) -> None:
        """Bind the current library session People surface."""

        self._unbind_people_index_coordinator()
        self._services = self._services.with_service("people_service", people_service)
        if people_service is None:
            return
        coordinator = people_service.coordinator
        if coordinator is None:
            return
        coordinator.resume()
        coordinator.snapshotCommitted.connect(
            self._on_people_snapshot_committed, Qt.ConnectionType.QueuedConnection
        )
        self._people_index_coordinator = coordinator

    @property
    def people_service(self) -> PeopleService | None:
        return self._services.people_service

    def bind_map_runtime(self, map_runtime: "MapRuntimePort | None") -> None:
        """Bind the current library session Maps runtime surface."""

        self._services = self._services.with_service("map_runtime", map_runtime)

    @property
    def map_runtime(self) -> "MapRuntimePort | None":
        return self._services.map_runtime

    def bind_map_interaction_service(
        self,
        map_interaction_service: "MapInteractionServicePort | None",
    ) -> None:
        """Bind the current library session Maps interaction surface."""

        self._services = self._services.with_service("map_interaction_service", map_interaction_service)

    @property
    def map_interaction_service(self) -> "MapInteractionServicePort | None":
        return self._services.map_interaction_service

    def bind_edit_service(self, edit_service: "EditServicePort | None") -> None:
        """Bind the current library session edit surface."""

        self._services = self._services.with_service("edit_service", edit_service)

    @property
    def edit_service(self) -> "EditServicePort | None":
        return self._services.edit_service

    def bind_location_service(
        self,
        location_service: "LocationAssetServicePort | None",
    ) -> None:
        """Bind the current library session Location query surface."""

        self._services = self._services.with_service("location_service", location_service)
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None

    @property
    def location_service(self) -> "LocationAssetServicePort | None":
        return self._services.location_service

