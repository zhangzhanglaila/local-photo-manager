"""Coordinator that binds sidebar and map widgets to GalleryViewModel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from ...i18n import tr

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.config import ALL_PHOTOS_TITLE
from iPhoto.gui.services.pinned_items_service import PinnedItemsService, PinnedSidebarItem
from iPhoto.gui.services.people_service_resolver import resolve_people_service
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.facade import AppFacade
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from ...utils.logging import diag

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator


class NavigationCoordinator(QObject):
    """Thin binder around gallery navigation and location flows."""

    bindLibraryRequested = Signal()
    _logger = logging.getLogger(__name__)

    def __init__(
        self,
        sidebar: AlbumSidebar,
        router: ViewRouter,
        gallery_vm: GalleryViewModel,
        context: RuntimeEntryContract,
        facade: AppFacade,
        pinned_items_service: PinnedItemsService | None = None,
        grid_view: object | None = None,
    ) -> None:
        super().__init__()
        self._sidebar = sidebar
        self._router = router
        self._gallery_vm = gallery_vm
        self._context = context
        self._facade = facade
        self._pinned_items_service = pinned_items_service
        self._grid_view = grid_view

        self._playback_coordinator: Optional[PlaybackCoordinator] = None

        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason: Optional[Literal["edit", "operation"]] = None
        self._pending_detail_return: Path | None = None

        self._connect_signals()

    def set_playback_coordinator(self, coordinator: PlaybackCoordinator) -> None:
        self._playback_coordinator = coordinator

    def _connect_signals(self) -> None:
        self._sidebar.albumSelected.connect(self.open_album)
        self._sidebar.pinnedItemSelected.connect(self.open_pinned_item)
        self._sidebar.allPhotosSelected.connect(self.open_all_photos)
        self._sidebar.staticNodeSelected.connect(self._handle_static_node)
        self._sidebar.bindLibraryRequested.connect(self._handle_bind_library)
        self._sidebar.excludeAlbumRequested.connect(self._handle_exclude_album)

        self._gallery_vm.route_requested.connect(self._handle_route_requested)
        self._gallery_vm.detail_requested.connect(self._handle_detail_requested)
        self._gallery_vm.bind_library_requested.connect(self._handle_bind_library)
        self._gallery_vm.map_assets_changed.connect(self._handle_map_assets_changed)
        self._gallery_vm.cluster_gallery_mode_changed.connect(self._handle_cluster_gallery_mode_changed)
        self._gallery_vm.sidebar_path_requested.connect(self._sidebar.select_path)

    def open_album(self, path: Path) -> None:
        if self._should_treat_as_refresh(path):
            return
        self._reset_playback()
        self._gallery_vm.open_album(path)

    def open_pinned_item(self, pinned_item: PinnedSidebarItem) -> None:
        self._reset_playback()
        library_root = self._context.library.root()
        if pinned_item.kind == "album":
            target = Path(pinned_item.item_id)
            if not target.exists():
                self._handle_missing_pinned_item(
                    pinned_item,
                    library_root=library_root,
                    message=f"Pinned album '{pinned_item.label or target.name}' is no longer available and will be removed from the sidebar.",
                )
                return
            self._gallery_vm.open_pinned_album(target)
            return

        if library_root is None:
            self.bindLibraryRequested.emit()
            return

        people_service = resolve_people_service(
            self._context.library,
            library_root=library_root,
        )
        if people_service is None:
            self._logger.warning(
                "Pinned %s '%s' requested without an active People service",
                pinned_item.kind,
                pinned_item.item_id,
            )
            return
        if pinned_item.kind == "person":
            try:
                query = people_service.build_cluster_query(pinned_item.item_id)
                entity_exists = people_service.has_cluster(pinned_item.item_id)
            except Exception:
                self._logger.warning(
                    "Failed to open pinned person '%s' (%s)",
                    pinned_item.label,
                    pinned_item.item_id,
                    exc_info=True,
                )
                return
            if not query.asset_ids and not entity_exists:
                self._handle_missing_pinned_item(
                    pinned_item,
                    library_root=library_root,
                    message=f"Pinned person '{pinned_item.label}' is no longer available and will be removed from the sidebar.",
                )
                return
            self._gallery_vm.open_pinned_people_query(
                query,
                kind="person",
                entity_id=pinned_item.item_id,
            )
            return

        if pinned_item.kind == "group":
            try:
                query = people_service.build_group_query(pinned_item.item_id)
                entity_exists = people_service.has_group(pinned_item.item_id)
            except Exception:
                self._logger.warning(
                    "Failed to open pinned group '%s' (%s)",
                    pinned_item.label,
                    pinned_item.item_id,
                    exc_info=True,
                )
                return
            if not query.asset_ids and not entity_exists:
                self._handle_missing_pinned_item(
                    pinned_item,
                    library_root=library_root,
                    message=f"Pinned group '{pinned_item.label}' is no longer available and will be removed from the sidebar.",
                )
                return
            self._gallery_vm.open_pinned_people_query(
                query,
                kind="group",
                entity_id=pinned_item.item_id,
            )

    def _handle_missing_pinned_item(
        self,
        pinned_item: PinnedSidebarItem,
        *,
        library_root: Path | None,
        message: str,
    ) -> None:
        dialogs.show_warning(self._sidebar, message)
        if self._pinned_items_service is None:
            return
        if pinned_item.kind == "album":
            self._pinned_items_service.prune_missing_album(
                Path(pinned_item.item_id),
                library_root=library_root,
            )
            return
        self._pinned_items_service.prune_missing_entity(
            kind=pinned_item.kind,
            item_id=pinned_item.item_id,
            library_root=library_root,
        )

    def open_all_photos(self) -> None:
        import logging
        diag("[DIAG] navigation_coordinator.open_all_photos called")
        self._reset_playback()
        self._gallery_vm.open_all_photos()
        self._set_grid_empty_mode(None)

    def open_recently_deleted(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_recently_deleted()

    def open_location_view(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_location_map()
        map_view = self._router.map_view()
        if map_view is not None:
            set_focused = getattr(map_view, "set_focused_asset_mode", None)
            if callable(set_focused):
                set_focused(False)

    def open_people_view(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_people_dashboard()

    def open_cluster_gallery(self, assets: list) -> None:
        self._reset_playback()
        self._gallery_vm.open_cluster_gallery(assets)

    def open_people_cluster_gallery(self, query) -> None:
        self._reset_playback()
        self._gallery_vm.open_people_cluster_gallery(query)
        self._router.show_gallery()

    def return_from_cluster_gallery(self) -> None:
        self._gallery_vm.return_from_cluster_gallery()

    def return_to_map_from_cluster_gallery(self) -> None:
        self.return_from_cluster_gallery()

    def open_location_asset(self, rel: str) -> None:
        self._gallery_vm.open_location_asset(rel)

    def _handle_static_node(self, name: str) -> None:
        normalized = name.casefold()

        # Also check against translated header names so that translated
        # sidebar titles (e.g. "相册" for "Albums") are recognised.
        if normalized == "all photos":
            self.open_all_photos()
            self._set_grid_empty_mode(None)
        elif normalized == "recently deleted":
            self.open_recently_deleted()
            self._set_grid_empty_mode(None)
        elif normalized == "albums" or name == tr("sidebar.albums"):
            self._reset_playback()
            self._gallery_vm.open_albums_dashboard()
        elif normalized == "favorites":
            self._reset_playback()
            self._gallery_vm.open_filtered_collection(name, is_favorite=True)
            self._set_grid_empty_mode("favorites")
        elif normalized == "videos":
            from iPhoto.domain.models.core import MediaType

            self._reset_playback()
            self._gallery_vm.open_filtered_collection(name, media_types=[MediaType.VIDEO])
            self._set_grid_empty_mode("videos")
        elif normalized == "live photos":
            from iPhoto.domain.models.core import MediaType

            self._reset_playback()
            self._gallery_vm.open_filtered_collection(name, media_types=[MediaType.LIVE_PHOTO])
            self._set_grid_empty_mode("live")
        elif normalized == "people":
            self.open_people_view()
        elif normalized == "location":
            if self._pending_detail_return is not None:
                self._pending_detail_return = None
                return
            self.open_location_view()

    def _handle_route_requested(self, view: str) -> None:
        if view == "gallery":
            self._router.show_gallery()
        elif view == "map":
            self._router.show_map()
        elif view == "people":
            self._router.show_people()
        elif view == "albums_dashboard":
            self._router.show_albums_dashboard()
        elif view == "detail":
            self._router.show_detail()

    def _handle_detail_requested(self, row: int) -> None:
        if self._playback_coordinator is not None:
            self._playback_coordinator.play_asset(row)

    def _handle_map_assets_changed(self, assets: list, root: Path) -> None:
        map_view = self._router.map_view()
        if map_view is not None:
            set_focused = getattr(map_view, "set_focused_asset_mode", None)
            if callable(set_focused):
                set_focused(self._gallery_vm.is_location_map_focused())
            map_view.set_assets(assets, root)

    def _handle_cluster_gallery_mode_changed(self, enabled: bool) -> None:
        gallery_page = self._router.gallery_page()
        if gallery_page is not None:
            gallery_page.set_cluster_gallery_mode(
                bool(enabled),
                back_tooltip=self._gallery_vm.cluster_gallery_back_tooltip(),
            )

    def _handle_bind_library(self) -> None:
        self.bindLibraryRequested.emit()

    def _handle_exclude_album(self, path: Path) -> None:
        """Add *path* to the root manifest's exclude list and rescan."""
        from ...config import ALBUM_MANIFEST_NAMES, DEFAULT_EXCLUDE
        from ...utils.jsonio import read_json, write_json
        from ...utils.pathutils import album_manifest_path, ensure_work_dir, find_album_manifest

        library_root = self._context.library.root()
        if library_root is None:
            self._logger.warning("_handle_exclude_album: no library root set")
            return

        # Excluding a library root means removing that root from the multi-root list.
        try:
            resolved_path = path.resolve()
        except OSError:
            resolved_path = path

        all_roots = self._context.library.roots()
        matching_root = None
        for r in all_roots:
            try:
                if r.resolve() == resolved_path:
                    matching_root = r
                    break
            except OSError:
                continue

        if matching_root is not None:
            self._logger.info("_handle_exclude_album: excluding library root %s", matching_root)
            from iPhoto.gui.ui.widgets import dialogs
            confirmed = dialogs.confirm_action(
                self._sidebar,
                tr("dialog.remove_library_msg", root=matching_root),
                title=tr("dialog.remove_library"),
                yes_label=tr("dialog.remove"),
                no_label=tr("generic.cancel"),
            )
            if not confirmed:
                return

            # 记录当前查看的根目录
            current_root = self._gallery_vm.active_root.value

            self._context.library.remove_root(matching_root)
            remaining = self._context.library.roots()
            self._context.settings.set("basic_library_paths", [str(r) for r in remaining])
            if remaining:
                self._context.settings.set("basic_library_path", str(remaining[0]))
            else:
                self._context.settings.set("basic_library_path", "")
                self._context.library.shutdown()
            self._sidebar.refresh_tree_model()

            # ⭐ 修复：删除相册后立即清空画廊，无论是否有剩余相册
            self._clear_gallery_after_removal()

            # ⭐ 修复：删除相册后刷新画廊视图
            if not remaining:
                # 如果没有剩余的根目录，导航到绑定库界面
                self.bindLibraryRequested.emit()
            elif current_root == matching_root:
                # 如果当前正在查看被删除的根目录，导航到"All Photos"
                # ⭐ 需要更新选中路径，移除已删除的相册
                self._update_checked_album_paths_after_removal(matching_root)
                self.open_all_photos()
            else:
                # 其他情况，刷新当前视图
                # ⭐ 也需要更新选中路径，移除已删除的相册
                self._update_checked_album_paths_after_removal(matching_root)
                self._gallery_vm.reload_current_selection()
            return

    def _clear_gallery_after_removal(self) -> None:
        """⭐ 删除相册后立即清空画廊显示"""
        # 清空当前选中的相册路径
        self._gallery_vm.set_checked_album_paths(None)
        # ⭐ 关键修复：同步更新 settings，避免旧路径残留
        self._context.settings.set("ui.checked_library_paths", [])
        # 清空当前视图的显示
        self._gallery_vm.clear_display()
        # 显示友好的空状态提示
        if self._grid_view is not None and hasattr(self._grid_view, "set_empty_mode"):
            self._grid_view.set_empty_mode("no_library")

    def _update_checked_album_paths_after_removal(self, removed_root: Path) -> None:
        """Update checked album paths after removing a library root."""
        # Get all remaining roots
        remaining = self._context.library.roots()
        if not remaining:
            # No remaining albums, clear checked paths
            self._gallery_vm.set_checked_album_paths(None)
            # ⭐ 关键修复：同步更新 settings，避免旧路径残留
            # 否则下次启动时 _restore_checked_library_paths 会恢复旧路径
            self._context.settings.set("ui.checked_library_paths", [])
            return

        # Assume remaining albums stay checked after removal
        remaining_paths = list(remaining)
        self._gallery_vm.set_checked_album_paths(remaining_paths)
        # ⭐ 关键修复：同步更新 settings，避免旧路径残留
        self._context.settings.set(
            "ui.checked_library_paths",
            [str(p) for p in remaining_paths],
        )

        # Find the root that contains this path.
        owning_root = library_root
        for r in all_roots:
            try:
                if path.resolve().is_relative_to(r.resolve()):
                    owning_root = r
                    break
            except OSError:
                continue

        # Find or create the root manifest, honouring the configured workspace.
        manifest_path = find_album_manifest(owning_root)
        if manifest_path is None:
            manifest_path = album_manifest_path(owning_root)
            manifest: dict = {
                "schema": "iPhoto/album@1",
                "title": owning_root.name,
                "filters": {},
            }
        else:
            try:
                manifest = read_json(manifest_path)
            except Exception:
                manifest = {"schema": "iPhoto/album@1", "title": owning_root.name, "filters": {}}

        # Compute the relative folder name (e.g. "202101202211").
        try:
            rel = path.resolve().relative_to(owning_root.resolve())
        except ValueError:
            self._logger.warning("_handle_exclude_album: path %s is not under root %s", path, owning_root)
            return
        folder_name = rel.parts[0] if rel.parts else None
        if not folder_name:
            self._logger.warning("_handle_exclude_album: could not determine folder name for %s", path)
            return

        self._logger.info("_handle_exclude_album: excluding folder '%s'", folder_name)
        exclude = manifest.setdefault("filters", {}).setdefault("exclude", list(DEFAULT_EXCLUDE))
        pattern = f"{folder_name}/**"
        if pattern not in exclude:
            exclude.append(pattern)
            write_json(manifest_path, manifest, backup_dir=ensure_work_dir(owning_root) / "manifest.bak")

        # Remove the folder's assets from the database and refresh the tree.
        self._context.library.scan_tree()
        from ...config import DEFAULT_INCLUDE
        self._facade.scan_root_async(
            owning_root,
            include=DEFAULT_INCLUDE,
            exclude=exclude,
        )

    def _should_treat_as_refresh(self, path: Path) -> bool:
        static_selection = self._gallery_vm.static_selection.value
        if static_selection is not None:
            return False
        if self._facade.current_album and self._facade.current_album.root.resolve() == path.resolve():
            return self._router.is_gallery_view_active()
        return False

    def _reset_playback(self) -> None:
        if self._playback_coordinator is not None:
            self._playback_coordinator.reset_for_gallery()

    def suppress_tree_refresh_for_edit(self) -> None:
        self._suppress_tree_refresh = True
        self._tree_refresh_suppression_reason = "edit"

    def should_suppress_tree_refresh(self) -> bool:
        return self._suppress_tree_refresh

    def release_tree_refresh_suppression_if_edit(self) -> None:
        if self._tree_refresh_suppression_reason == "edit":
            self._suppress_tree_refresh = False
            self._tree_refresh_suppression_reason = None

    def clear_tree_refresh_suppression(self) -> None:
        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason = None

    def invalidate_location_session(self) -> None:
        self._gallery_vm.invalidate_location_session()

    def is_location_context_active(self) -> bool:
        return self._gallery_vm.is_location_context_active()

    def is_in_cluster_gallery(self) -> bool:
        return self._gallery_vm.is_in_cluster_gallery()

    def suspend_library_watcher(self, duration: int = 250) -> None:
        manager = self._context.library
        manager.pause_watcher()
        QTimer.singleShot(duration, manager.resume_watcher)

    def pause_library_watcher(self) -> None:
        self._context.library.pause_watcher()

    def resume_library_watcher(self) -> None:
        self._context.library.resume_watcher()

    def static_selection(self) -> Optional[str]:
        return self._gallery_vm.static_selection.value

    def is_all_photos_view(self) -> bool:
        value = self.static_selection()
        return bool(value) and value.casefold() == ALL_PHOTOS_TITLE.casefold()

    def is_recently_deleted_view(self) -> bool:
        value = self.static_selection()
        return bool(value) and value.casefold() == "recently deleted"

    def sidebar_model(self):
        return self._sidebar.tree_model()

    def _set_grid_empty_mode(self, mode: str | None) -> None:
        """Notify the grid view of the current filter context for empty-state messages."""
        gv = self._grid_view
        if gv is not None and hasattr(gv, "set_empty_mode"):
            gv.set_empty_mode(mode)
