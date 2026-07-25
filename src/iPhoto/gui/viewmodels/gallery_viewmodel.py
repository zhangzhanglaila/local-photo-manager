"""Pure Python screen-level view model for gallery and map navigation."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

_logger = logging.getLogger(__name__)

from iPhoto.application.ports import AssetStateServicePort
from iPhoto.application.services.location_asset_service import (
    geotagged_asset_from_row,
)
from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.config import ALL_PHOTOS_TITLE, DEFAULT_EXCLUDE, DEFAULT_INCLUDE, RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.ui.menus.core import MenuContext
from iPhoto.gui.coordinators.location_selection_session import LocationSelectionSession
from iPhoto.gui.facade import AppFacade
from iPhoto.gui.services.location_trash_navigation_service import (
    LocationTrashNavigationService,
)
from iPhoto.gui.services.people_service_resolver import resolve_people_service

from .base import BaseViewModel
from .gallery_collection_store import GalleryCollectionStore
from .signal import ObservableProperty, Signal


class GalleryViewModel(BaseViewModel):
    """Own gallery, collection, and location-navigation state."""

    def __init__(
        self,
        *,
        store: GalleryCollectionStore,
        context: RuntimeEntryContract,
        facade: AppFacade,
        asset_state_service: AssetStateServicePort | None,
        location_session: LocationSelectionSession | None = None,
        location_trash_service: LocationTrashNavigationService | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._context = context
        self._facade = facade
        self._asset_state_service = asset_state_service
        self._location_session = location_session or LocationSelectionSession()
        self._location_trash_service = location_trash_service or LocationTrashNavigationService(
            library_manager_getter=lambda: self._context.library,
        )
        self._cluster_gallery_origin: Literal["location", "people", None] = None
        self._people_cluster_kind: Literal["person", "group", None] = None
        self._people_cluster_id: str | None = None
        self._cluster_gallery_opened_at: float = 0.0
        self._checked_album_paths: list[str] | None = None
        self._focused_location_rel: str | None = None

        self.current_section = ObservableProperty("gallery")
        self.static_selection = ObservableProperty(None)
        self.selection_mode = ObservableProperty(False)
        self.active_root = ObservableProperty(context.library.root())
        self.current_query = ObservableProperty(None)
        self.current_direct_assets = ObservableProperty(None)
        self.can_return_to_map = ObservableProperty(False)

        self.route_requested = Signal()
        self.detail_requested = Signal()
        self.bind_library_requested = Signal()
        self.map_assets_changed = Signal()
        self.cluster_gallery_mode_changed = Signal()
        self.sidebar_path_requested = Signal()
        self.message_requested = Signal()

        self._location_trash_service.locationAssetsLoaded.connect(
            self._handle_location_assets_loaded
        )
        self._location_trash_service.errorRaised.connect(
            lambda message: self.message_requested.emit(message, 3000)
        )

    @property
    def location_session(self) -> LocationSelectionSession:
        return self._location_session

    def bind_asset_state_service(
        self,
        asset_state_service: AssetStateServicePort | None,
    ) -> None:
        self._asset_state_service = asset_state_service

    def set_checked_album_paths(self, paths: list[Path] | None) -> None:
        """Store the currently checked root folder paths for query filtering."""
        if paths is None:
            self._checked_album_paths = None
        else:
            self._checked_album_paths = [str(p.resolve()) for p in paths]

    def _has_unchecked_folders(self) -> bool:
        """Return True if any root folder is unchecked (not in _checked_album_paths)."""
        if not self._checked_album_paths:
            return False
        library = self._context.library
        roots = library.roots()
        if not roots:
            return False
        checked_set = {p.lower().replace("\\", "/") for p in self._checked_album_paths}
        for r in roots:
            r_str = str(r.resolve()).lower().replace("\\", "/")
            if r_str not in checked_set:
                return True
        return False

    def on_album_check_state_changed(self, checked_paths: list[Path]) -> None:
        """Handle sidebar checkbox changes — refresh current view if applicable."""
        self.set_checked_album_paths(checked_paths if checked_paths else None)
        section = self.current_section.value
        if section == "all_photos":
            self.open_all_photos()
        elif section == "location_map":
            self._location_session.invalidate()
            self.open_location_map()

    def open_album(self, path: Path, *, select_sidebar_path: bool = True) -> None:
        album = self._facade.open_album(path)
        active_root = album.root if album else path
        if album:
            self._context.remember_album(album.root)
            if select_sidebar_path:
                self.sidebar_path_requested.emit(album.root)
        else:
            if select_sidebar_path:
                self.sidebar_path_requested.emit(path)

        query = AssetQuery(album_path=self._album_path_for_query(active_root))
        query.include_subalbums = True
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section="album",
            static_selection=None,
            root=active_root,
            query=query,
        )

    def open_pinned_album(self, path: Path) -> None:
        album = self._facade.open_album(path)
        active_root = album.root if album else path
        if album:
            self._context.remember_album(album.root)

        query = AssetQuery(album_path=self._album_path_for_query(active_root))
        query.include_subalbums = True
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section="pinned_album",
            static_selection="Pinned",
            root=active_root,
            query=query,
        )

    def open_all_photos(self) -> None:
        import logging
        logging.getLogger(__name__).info("[DIAG] GalleryViewModel.open_all_photos called")
        root = self._context.library.root()
        if root is None:
            logging.getLogger(__name__).warning("[DIAG] open_all_photos: library root is None!")
            self.bind_library_requested.emit()
            return
        logging.getLogger(__name__).info("[DIAG] open_all_photos: root=%s", root)
        self._clear_cluster_gallery_context()
        query = AssetQuery()
        # Only use album_paths filter when a proper subset is checked.
        # When all folders are checked (or none), use the fast SQL path.
        # ⭐ 关键修复：如果 _checked_album_paths 中的路径与当前 library.roots()
        # 不匹配（来自旧的 settings），清除它们以避免过滤掉所有照片
        if self._checked_album_paths and self._has_unchecked_folders():
            # 验证 checked paths 中至少有一个匹配当前 root
            library_roots = self._context.library.roots()
            root_set = {str(p.resolve()).lower().replace("\\", "/") for p in library_roots}
            valid_checked = [
                p for p in self._checked_album_paths
                if p.lower().replace("\\", "/") in root_set
                or any(p.lower().replace("\\", "/").startswith(r + "/") for r in root_set)
                or any(r.startswith(p.lower().replace("\\", "/") + "/") for r in root_set)
            ]
            if valid_checked:
                query.album_paths = valid_checked
            else:
                logging.getLogger(__name__).warning(
                    "[DIAG] open_all_photos: _checked_album_paths=%s 不匹配当前 root=%s，清除",
                    self._checked_album_paths, root,
                )
                self._checked_album_paths = None
        self._load_query(
            section="all_photos",
            static_selection=ALL_PHOTOS_TITLE,
            root=root,
            query=query,
        )
        # Populate the map with geotagged assets so the map view stays useful.
        self._location_session.set_mode("map")
        if (
            self._location_session.root == root
            and self._location_session.has_snapshot
            and not self._location_session.invalidated
        ):
            cached = self._location_session.full_assets()
            self.map_assets_changed.emit(self._filter_assets_by_checked_paths(cached), root)
        else:
            self._request_location_assets(root)

    def open_recently_deleted(self) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        deleted_root = self._location_trash_service.prepare_recently_deleted()
        if deleted_root is None:
            return
        self._facade.open_album(deleted_root)
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section="recently_deleted",
            static_selection="Recently Deleted",
            root=deleted_root,
            query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME),
        )

    def open_filtered_collection(
        self,
        title: str,
        *,
        is_favorite: bool | None = None,
        media_types: list[MediaType] | None = None,
    ) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        query = AssetQuery()
        if is_favorite:
            query.is_favorite = True
        if media_types:
            query.media_types = list(media_types)
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section=title.casefold().replace(" ", "_"),
            static_selection=title,
            root=root,
            query=query,
        )

    def open_albums_dashboard(self) -> None:
        root = self._context.library.root()
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self.current_section.value = "dashboard"
        self.static_selection.value = "Albums"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self.route_requested.emit("albums_dashboard")

    def open_people_dashboard(self) -> None:
        root = self._context.library.root()
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self.current_section.value = "people_dashboard"
        self.static_selection.value = "People"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self.route_requested.emit("people")

    def open_location_map(self) -> None:
        root = self._context.library.root()
        if root is None:
            _logger.warning("open_location_map: library root is None")
            self.bind_library_requested.emit()
            return
        self._focused_location_rel = None
        _logger.debug("open_location_map: root=%s", root)
        self.current_section.value = "location_map"
        self.static_selection.value = "Location"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self._clear_cluster_gallery_context()
        self._location_session.set_mode("map")
        self.route_requested.emit("map")

        if (
            self._location_session.root == root
            and self._location_session.has_snapshot
            and not self._location_session.invalidated
        ):
            cached = self._location_session.full_assets()
            _logger.debug("open_location_map: using cached assets, count=%d", len(cached))
            self.map_assets_changed.emit(self._filter_assets_by_checked_paths(cached), root)
            return

        _logger.debug("open_location_map: requesting fresh assets")
        self._request_location_assets(root)

    def open_location_map_focused(self, asset: object) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        rel = getattr(asset, "library_relative", None)
        if not isinstance(rel, str) or not rel:
            return
        self._focused_location_rel = Path(rel).as_posix()
        self.current_section.value = "location_map"
        self.static_selection.value = "Location"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self._clear_cluster_gallery_context()
        self._location_session.set_mode("map")
        self._location_session.upsert_asset(asset)
        self.route_requested.emit("map")
        self.map_assets_changed.emit([asset], root)

    def show_all_location_assets(self) -> None:
        self.open_location_map()

    def is_location_map_focused(self) -> bool:
        return bool(self._focused_location_rel)

    def open_location_asset(self, rel: str) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        if (
            self._location_session.root != root
            or not self._location_session.has_snapshot
            or self._location_session.invalidated
        ):
            return
        asset = self._location_session.resolve_asset(rel)
        if asset is None:
            return
        self.open_cluster_gallery([asset])

    def open_cluster_gallery(self, assets: list[Any]) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self._cluster_gallery_origin = "location"
        self.current_section.value = "cluster_gallery"
        self.static_selection.value = "Location"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = list(assets)
        self.can_return_to_map.value = True
        self._location_session.set_mode("cluster_gallery")
        self._store.load_selection(root, direct_assets=assets, library_root=root)
        self.cluster_gallery_mode_changed.emit(True)
        self.route_requested.emit("gallery")

    def open_people_cluster_gallery(
        self,
        query: AssetQuery,
        *,
        kind: Literal["person", "group", None] = None,
        entity_id: str | None = None,
    ) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self._cluster_gallery_origin = "people"
        self._people_cluster_kind = kind
        self._people_cluster_id = entity_id if entity_id else None
        self._cluster_gallery_opened_at = time.monotonic()
        self._location_session.set_mode("inactive")
        self.current_section.value = "people_cluster_gallery"
        self.static_selection.value = "People"
        self.active_root.value = root
        self.current_query.value = query
        self.current_direct_assets.value = None
        self.can_return_to_map.value = True
        self._store.load_selection(root, query=query)
        self.cluster_gallery_mode_changed.emit(True)

    def open_pinned_people_query(
        self,
        query: AssetQuery,
        *,
        kind: Literal["person", "group", None] = None,
        entity_id: str | None = None,
    ) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._people_cluster_kind = kind
        self._people_cluster_id = entity_id if entity_id else None
        self.current_section.value = "pinned_people_gallery"
        self.static_selection.value = "Pinned"
        self.active_root.value = root
        self.current_query.value = query
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self._store.load_selection(root, query=query)
        self.cluster_gallery_mode_changed.emit(False)
        self.route_requested.emit("gallery")

    def return_from_cluster_gallery(self) -> None:
        if self._cluster_gallery_origin == "location" or (
            self._cluster_gallery_origin is None
            and self._location_session.mode == "cluster_gallery"
        ):
            self.cluster_gallery_mode_changed.emit(False)
            self.open_location_map()
            return
        if self._cluster_gallery_origin == "people":
            self.cluster_gallery_mode_changed.emit(False)
            self._clear_cluster_gallery_context()
            self.open_people_dashboard()

    def handle_people_snapshot_committed(self, event: object) -> None:
        if self._cluster_gallery_origin != "people" and self.current_section.value != "pinned_people_gallery":
            return
        if self._people_cluster_kind not in {"person", "group"} or not self._people_cluster_id:
            return
        _GRACE_PERIOD = 3.0
        if time.monotonic() - self._cluster_gallery_opened_at < _GRACE_PERIOD:
            return
        root = self._context.library.root()
        if root is None:
            return
        if getattr(event, "library_root", None) != root:
            return

        current_id = self._people_cluster_id
        current_query = self.current_query.value

        if self._people_cluster_kind == "person":
            redirects = getattr(event, "person_redirects", {}) or {}
            changed_ids = set(getattr(event, "changed_person_ids", ()) or ())
        else:
            redirects = getattr(event, "group_redirects", {}) or {}
            changed_ids = set(getattr(event, "changed_group_ids", ()) or ())

        changed_asset_ids = set(getattr(event, "changed_asset_ids", ()) or ())
        asset_ids = getattr(current_query, "asset_ids", ()) if current_query is not None else ()
        affected = (
            current_id in changed_ids
            or current_id in redirects
        )
        if not affected:
            return

        current_id = redirects.get(current_id, current_id)

        if not current_id:
            self.return_from_cluster_gallery()
            return

        service = resolve_people_service(
            self._context.library,
            library_root=root,
        )
        if service is None:
            return
        if self._people_cluster_kind == "person":
            if not service.has_cluster(current_id):
                self.return_from_cluster_gallery()
                return
            query = service.build_cluster_query(current_id)
        else:
            if not service.has_group(current_id):
                self.return_from_cluster_gallery()
                return
            query = service.build_group_query(current_id)

        if not query.asset_ids:
            self._store.reload_current_selection()
            return

        self._people_cluster_id = current_id
        self.current_query.value = query
        self._store.load_selection(root, query=query)

    def return_to_map_from_cluster_gallery(self) -> None:
        self.return_from_cluster_gallery()

    def handle_location_scan_chunk(self, scan_root: Path, chunk: list[dict]) -> None:
        root = self._context.library.root()
        if root is None or not self._scan_root_matches_location_context(scan_root, root):
            return
        if self._location_session.mode == "inactive":
            if self._location_session.root == root and self._location_session.has_snapshot:
                self._location_session.invalidate()
            return

        changed = False
        for row in chunk:
            asset = self._location_asset_from_row(row, root)
            if asset is not None:
                changed = self._location_session.upsert_asset(asset) or changed
                continue
            rel = row.get("rel") if isinstance(row, dict) else None
            if isinstance(rel, str) and rel:
                changed = self._location_session.remove_asset(rel) or changed

        if changed and self._location_session.mode == "map":
            assets = self._focused_location_assets()
            if assets is None:
                assets = self._location_session.full_assets()
            self.map_assets_changed.emit(assets, root)

    def handle_location_scan_finished(self, scan_root: Path, success: bool) -> None:
        if not success:
            return
        root = self._context.library.root()
        if root is None or not self._scan_root_matches_location_context(scan_root, root):
            return
        if self._location_session.mode == "inactive":
            if self._location_session.root == root and self._location_session.has_snapshot:
                self._location_session.invalidate()
            return

        self._request_location_assets(root)

    def _request_location_assets(self, root: Path) -> None:
        request = self._location_trash_service.request_location_assets()
        if request is None:
            _logger.warning("_request_location_assets: request_location_assets returned None")
            return
        serial, request_root = request
        _logger.debug("_request_location_assets: serial=%d root=%s", serial, request_root)
        if request_root != root:
            root = request_root
        self._location_session.begin_load_with_serial(root, serial)

    def _location_asset_from_row(self, row: object, root: Path):
        location_service = getattr(self._context.library, "location_service", None)
        asset_from_row = getattr(location_service, "asset_from_row", None)
        if callable(asset_from_row):
            return asset_from_row(row)
        return geotagged_asset_from_row(root, row)

    def _handle_location_assets_loaded(
        self,
        serial: int,
        root: Path,
        assets: list,
    ) -> None:
        _logger.debug("_handle_location_assets_loaded: serial=%d root=%s assets=%d", serial, root, len(assets))
        filtered = self._filter_assets_by_checked_paths(assets)
        if not self._location_session.accept_loaded(serial, root, filtered):
            _logger.warning("_handle_location_assets_loaded: accept_loaded returned False")
            return
        if self._location_session.mode == "map":
            assets = self._focused_location_assets()
            if assets is None:
                assets = self._location_session.full_assets()
            _logger.debug("_handle_location_assets_loaded: emitting map_assets_changed, count=%d", len(assets))
            self.map_assets_changed.emit(assets, root)

    def _filter_assets_by_checked_paths(self, assets: list) -> list:
        """Filter assets to only include those under checked root paths."""
        if not self._checked_album_paths or not self._has_unchecked_folders():
            return assets
        prefixes = tuple(p.rstrip("/").lower() + "/" for p in self._checked_album_paths)
        exact = tuple(p.rstrip("/").lower() for p in self._checked_album_paths)
        filtered = []
        for asset in assets:
            abs_path = getattr(asset, "absolute_path", None)
            if abs_path is None:
                continue
            path_str = str(abs_path).replace("\\", "/").lower()
            if path_str in exact or path_str.startswith(prefixes):
                filtered.append(asset)
        return filtered

    def handle_album_renamed(self, old_path: Path, new_path: Path) -> None:
        if self.current_section.value not in {"album", "pinned_album"}:
            return
        active_root = self.active_root.value
        if active_root is None:
            return
        retargeted_path = self._retarget_renamed_path(active_root, old_path, new_path)
        if retargeted_path is None:
            return

        section = self.current_section.value
        static_selection = self.static_selection.value
        album = self._facade.open_album(retargeted_path)
        retargeted_root = album.root if album else retargeted_path
        if album:
            self._context.remember_album(album.root)

        query = AssetQuery(album_path=self._album_path_for_query(retargeted_root))
        query.include_subalbums = True
        self._load_query(
            section=section,
            static_selection=static_selection,
            root=retargeted_root,
            query=query,
        )

    def on_library_tree_updated(self) -> bool:
        self._location_session.invalidate()
        if self.is_location_context_active():
            return False
        self._store.reload_current_selection()
        return True

    def set_selection_mode(self, enabled: bool) -> None:
        self.selection_mode.value = bool(enabled)

    def invalidate_location_session(self) -> None:
        self._location_session.invalidate()

    def is_location_context_active(self) -> bool:
        return self._location_session.mode != "inactive"

    def is_in_cluster_gallery(self) -> bool:
        return self._cluster_gallery_origin is not None or self._location_session.mode == "cluster_gallery"

    def cluster_gallery_back_tooltip(self) -> str:
        if self._cluster_gallery_origin == "people":
            return "Return to People"
        return "Return to Map"

    def open_row(self, row: int) -> None:
        if row < 0:
            return
        self.detail_requested.emit(row)

    def rescan_current(self) -> None:
        if self._facade.current_album is not None:
            self._facade.rescan_current_async()
            return

        library_root = self._context.library.root()
        if library_root is None:
            self.message_requested.emit("No album is currently open.", 3000)
            return

        self._facade.scan_root_async(
            library_root,
            include=DEFAULT_INCLUDE,
            exclude=DEFAULT_EXCLUDE,
        )

    def path_for_row(self, row: int) -> Optional[Path]:
        dto = self._store.asset_at(row)
        return dto.abs_path if dto is not None else None

    def paths_for_rows(self, rows: Iterable[int]) -> list[Path]:
        seen: set[Path] = set()
        paths: list[Path] = []
        for row in rows:
            path = self.path_for_row(row)
            if path is None or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def items_for_rows(self, rows: Iterable[int]) -> list:
        items: list = []
        seen_rows: set[int] = set()
        for row in rows:
            if row in seen_rows or row < 0:
                continue
            seen_rows.add(row)
            dto = self._store.asset_at(row)
            if dto is not None:
                items.append(dto)
        return items

    def context_menu_state(self) -> MenuContext:
        section = self.current_section.value
        entity_kind: str | None = None
        entity_id: str | None = None
        if section in {"album", "pinned_album"} and self.active_root.value is not None:
            entity_kind = "album"
            entity_id = str(self.active_root.value)
        elif section in {"people_cluster_gallery", "pinned_people_gallery"}:
            if self._people_cluster_kind in {"person", "group"}:
                entity_kind = self._people_cluster_kind
                entity_id = self._people_cluster_id

        return MenuContext(
            surface="gallery",
            selection_kind="empty",
            gallery_section=section,
            entity_kind=entity_kind,
            entity_id=entity_id,
            active_root=self.active_root.value,
            is_recently_deleted=section == "recently_deleted",
            is_cluster_gallery=section in {
                "cluster_gallery",
                "people_cluster_gallery",
                "pinned_people_gallery",
            },
        )

    def toggle_favorite_row(self, row: int) -> Optional[bool]:
        path = self.path_for_row(row)
        if path is None or self._asset_state_service is None:
            return None
        new_state = self._asset_state_service.toggle_favorite(path)
        self._store.update_favorite_status(row, new_state)
        return new_state

    def _load_query(
        self,
        *,
        section: str,
        static_selection: str | None,
        root: Path,
        query: AssetQuery,
    ) -> None:
        import logging
        logging.getLogger(__name__).info(
            "[DIAG] GalleryViewModel._load_query: section=%s root=%s store_count_before=%s",
            section, root, self._store.count(),
        )
        self._store.cancel_pending_load()
        self.current_section.value = section
        self.static_selection.value = static_selection
        self.active_root.value = root
        self.current_query.value = query
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        # ⭐ 立即加载查询而不是延迟，以便在扫描时能显示照片
        # 如果扫描正在进行，handle_scan_chunk 会更新显示
        self._store.load_selection(root, query=query)
        self.cluster_gallery_mode_changed.emit(False)
        self.route_requested.emit("gallery")
        logging.getLogger(__name__).info(
            "[DIAG] GalleryViewModel._load_query: after load_selection store_count=%s",
            self._store.count(),
        )

    def show_search_results(self, asset_ids: list[str]) -> None:
        """Display search results in the gallery.

        Parameters
        ----------
        asset_ids : list[str]
            List of asset IDs to display, ordered by relevance.
        """
        if not asset_ids:
            self.message_requested.emit("No search results", 3000)
            return

        root = self._context.library.root()
        query = AssetQuery(asset_ids=asset_ids)

        self._store.cancel_pending_load()
        self.current_section.value = "search_results"
        self.static_selection.value = "Search Results"
        self.active_root.value = root
        self.current_query.value = query
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False

        # Load the search results
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._store.load_selection(root, query=query))
        self.cluster_gallery_mode_changed.emit(False)
        self.route_requested.emit("gallery")

    def clear_display(self) -> None:
        """Clear the gallery display by resetting the store state."""
        self._store.cancel_pending_load()
        # 重置存储状态以清空显示
        old_count = self._store.count()
        self._store._reset_window_state()
        # 发出信号通知视图更新
        self._store.count_changed.emit(old_count, 0)
        self._store.data_changed.emit()
        # 清空视图模型状态
        self.current_section.value = "empty"
        self.static_selection.value = None
        self.active_root.value = None
        self.current_query.value = None
        self.current_direct_assets.value = []
        self.can_return_to_map.value = False
        self.cluster_gallery_mode_changed.emit(False)
        self.route_requested.emit("gallery")

    def _clear_location_context(self) -> None:
        self._location_session.set_mode("inactive")
        self.can_return_to_map.value = False
        self._focused_location_rel = None

    def _clear_cluster_gallery_context(self) -> None:
        self._cluster_gallery_origin = None
        self._people_cluster_kind = None
        self._people_cluster_id = None

    def _focused_location_assets(self) -> list | None:
        rel = self._focused_location_rel
        if not rel:
            return None
        asset = self._location_session.resolve_asset(rel)
        return [asset] if asset is not None else []

    def _album_path_for_query(self, path: Path) -> Optional[str]:
        library_root = self._context.library.root()
        if library_root is None:
            return path.name
        try:
            rel = path.resolve().relative_to(library_root.resolve())
        except (OSError, ValueError):
            try:
                rel = path.relative_to(library_root)
            except ValueError:
                return path.resolve().as_posix()
        rel_str = rel.as_posix()
        if rel_str in ("", "."):
            return None
        return rel_str

    def _scan_root_matches_location_context(self, scan_root: Path, root: Path) -> bool:
        try:
            scan_root_resolved = scan_root.resolve()
            root_resolved = root.resolve()
        except OSError:
            return False
        if scan_root_resolved == root_resolved or root_resolved in scan_root_resolved.parents:
            return True
        # In a multi-root library, a scan of any registered root should
        # refresh the location session because geotagged assets from all
        # roots are shown on the map.
        library = self._context.library
        for candidate in library.roots():
            try:
                if candidate.resolve() == scan_root_resolved:
                    return True
            except OSError:
                if candidate == scan_root:
                    return True
        return False

    def _paths_equal(self, first: Path, second: Path) -> bool:
        if first == second:
            return True
        try:
            return first.resolve() == second.resolve()
        except OSError:
            return False

    def _retarget_renamed_path(
        self,
        active_root: Path,
        old_path: Path,
        new_path: Path,
    ) -> Path | None:
        if self._paths_equal(active_root, old_path):
            return new_path

        for use_resolve in (True, False):
            try:
                active_candidate = active_root.resolve() if use_resolve else active_root
                old_candidate = old_path.resolve() if use_resolve else old_path
                rel = active_candidate.relative_to(old_candidate)
            except (OSError, ValueError):
                continue
            if rel.as_posix() in ("", "."):
                return new_path
            return new_path / rel
        return None
