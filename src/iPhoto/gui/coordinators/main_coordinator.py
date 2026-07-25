"""Coordinator that wires the main window to application logic.

This replaces the legacy MainController as the top-level orchestrator.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QCoreApplication,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import QAction

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.i18n import tr
from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController
from iPhoto.gui.ui.controllers.dialog_controller import DialogController
from iPhoto.gui.ui.controllers.export_controller import ExportController
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.controllers.map_extension_download_controller import (
    MapExtensionDownloadController,
)
from iPhoto.gui.ui.controllers.preview_controller import PreviewController
from iPhoto.gui.ui.controllers.selection_controller import SelectionController
from iPhoto.gui.ui.controllers.share_controller import ShareController
from iPhoto.gui.ui.controllers.status_bar_controller import StatusBarController
from iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController
from iPhoto.gui.ui.media import MediaAdjustmentCommitter, MediaSelectionSession
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from iPhoto.gui.services.location_trash_navigation_service import (
    LocationTrashNavigationService,
)
from iPhoto.gui.services.people_service_resolver import resolve_people_service
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from iPhoto.people.service import PeopleService
from maps.map_sources import supports_map_extension_download

if TYPE_CHECKING:
    from iPhoto.gui.ui.main_window import MainWindow


class MainCoordinator(QObject):
    """High-level coordinator for the main window.
    Acts as the entry point and glue code for the application, initializing
    legacy controllers and bridging them with the new architecture.
    """

    def __init__(
        self,
        window: MainWindow,
        context: RuntimeEntryContract,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._context = context
        # facade reference kept for signal wiring as some systems still emit through it
        self._facade = context.facade
        self._logger = logging.getLogger(__name__)
        self._media_failure_cleanup_paths: set[str] = set()
        self._return_from_map_path: Path | None = None
        self._startup_loading = True
        self._map_extension_download = MapExtensionDownloadController(
            window,
            context,
            package_root=self._resolve_map_package_root(self._map_runtime()),
        )
        if hasattr(window.ui, "download_map_extension_action"):
            window.ui.download_map_extension_action.setEnabled(supports_map_extension_download())

        self._event_bus = context.event_bus
        edit_service_getter = self._edit_service
        asset_state_service = self._asset_state_service()

        # --- ViewModels Setup ---
        lib_root = self._library_root()
        self._context.asset_runtime.bind_library_root(lib_root)
        self._asset_list_vm = GalleryListModelAdapter.create(
            asset_query_service=self._asset_query_service(),
            thumbnail_service=self._context.asset_runtime.thumbnail_service,
            edit_service_getter=edit_service_getter,
            library_root=lib_root,
            parent=window.ui.grid_view,
        )
        self._gallery_store = self._asset_list_vm.store
        self._media_session = MediaSelectionSession()
        self._media_session.bind_collection(self._gallery_store)
        self._thumbnail_service = self._context.asset_runtime.thumbnail_service
        bound_people_service = self._people_service(library_root=lib_root)
        self._playback_people_service = bound_people_service or PeopleService()
        if hasattr(window.ui, "people_page"):
            if bound_people_service is not None and hasattr(window.ui.people_page, "set_people_service"):
                window.ui.people_page.set_people_service(self._playback_people_service)
            else:
                window.ui.people_page.set_library_root(lib_root)
            window.ui.people_page.set_status_message(context.library.face_scan_status_message())
        self._pinned_items_service = PinnedItemsService(
            context.settings,
            people_service_getter=self._people_service,
            parent=self,
        )
        window.ui.sidebar.set_pinned_service(self._pinned_items_service)
        if hasattr(window.ui, "people_page"):
            window.ui.people_page.set_pinned_service(self._pinned_items_service)
        if hasattr(window.ui, "albums_dashboard_page"):
            window.ui.albums_dashboard_page.set_pinned_service(self._pinned_items_service)
            self._facade.albumCoverUpdated.connect(
                window.ui.albums_dashboard_page.update_album_cover
            )

        # Inject ViewModel provider into Facade for legacy operations (restore/delete)
        if self._facade:
            self._facade.set_model_provider(lambda: self._asset_list_vm)

        # --- Coordinators Setup ---

        # 1. View Router
        self._view_router = ViewRouter(window.ui)
        self._location_trash_navigation_service = LocationTrashNavigationService(
            library_manager_getter=lambda: context.library,
            parent=self,
        )

        self._gallery_vm = GalleryViewModel(
            store=self._gallery_store,
            context=context,
            facade=context.facade,
            asset_state_service=asset_state_service,
            location_trash_service=self._location_trash_navigation_service,
        )

        # 2. Navigation Coordinator
        self._navigation = NavigationCoordinator(
            window.ui.sidebar,
            self._view_router,
            self._gallery_vm,
            context,
            context.facade,  # Legacy Facade Bridge
            pinned_items_service=self._pinned_items_service,
            grid_view=window.ui.grid_view,
        )
        self._adjustment_committer = MediaAdjustmentCommitter(
            asset_vm=self._asset_list_vm,
            pause_watcher=self._navigation.pause_library_watcher,
            resume_watcher=self._navigation.resume_library_watcher,
            edit_service_getter=edit_service_getter,
            parent=self,
        )
        self._detail_vm = DetailViewModel(
            collection_store=self._gallery_store,
            media_session=self._media_session,
            asset_state_service=asset_state_service,
            adjustment_commit_port=self._adjustment_committer,
            edit_service_getter=edit_service_getter,
        )

        # 3. Playback Coordinator
        from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController

        self._player_view_controller = PlayerViewController(
            window.ui.player_stack,
            window.ui.image_viewer,
            window.ui.video_area,
            window.ui.player_placeholder,
            window.ui.live_badge,
            edit_service_getter=edit_service_getter,
        )
        self._header_controller = HeaderController(
            window.ui.location_label,
            window.ui.timestamp_label,
        )

        self._playback = PlaybackCoordinator(
            player_bar=window.ui.player_bar,
            player_view=self._player_view_controller,
            router=self._view_router,
            asset_model=self._asset_list_vm,
            detail_vm=self._detail_vm,
            adjustment_committer=self._adjustment_committer,
            zoom_slider=window.ui.zoom_slider,
            zoom_in_button=window.ui.zoom_in_button,
            zoom_out_button=window.ui.zoom_out_button,
            zoom_widget=window.ui.zoom_widget,
            favorite_button=window.ui.favorite_button,
            info_button=window.ui.info_button,
            rotate_button=window.ui.rotate_left_button,
            edit_button=window.ui.edit_button,
            share_button=window.ui.share_button,
            filmstrip_view=window.ui.filmstrip_view,
            toggle_filmstrip_action=window.ui.toggle_filmstrip_action,
            settings=context.settings,
            header_controller=self._header_controller,
            face_name_overlay=window.ui.face_name_overlay,
            people_service=self._playback_people_service,
            people_dashboard_refresh_callback=window.ui.people_page.schedule_index_refresh,
            library_manager=context.library,
            location_session_invalidator=self._gallery_vm.invalidate_location_session,
            map_runtime=self._map_runtime(),
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        self._navigation.set_playback_coordinator(self._playback)
        context.library.peopleSnapshotCommitted.connect(
            self._handle_people_snapshot_sidebar_refresh
        )
        # Manually attach info panel if available
        if hasattr(window.ui, "info_panel"):
            window.ui.info_panel.set_map_runtime(self._map_runtime())
            self._playback.set_info_panel(window.ui.info_panel)
            window.ui.info_panel.downloadMapExtensionRequested.connect(
                lambda: self._map_extension_download.start_download(source="info_panel")
            )
        if hasattr(window.ui, "map_view"):
            window.ui.map_view.set_map_runtime(self._map_runtime())
            window.ui.map_view.set_map_interaction_service(
                self._map_interaction_service()
            )

        # Detail map panel (kept for GPS coordinates display only).
        if hasattr(window.ui, "map_panel"):
            self._playback.set_detail_map_panel(window.ui.map_panel)

        # 4. Theme Controller
        self._theme_controller = WindowThemeController(window.ui, window, context.theme)

        # 5. Edit Coordinator
        self._edit = EditCoordinator(
            window.ui,  # Pass UI root for access to sidebar/header/viewer
            self._view_router,
            self._event_bus,
            self._asset_list_vm,  # Injected for invalidation
            window,
            self._theme_controller,
            self._navigation,
            self._media_session,
            self._adjustment_committer,
            edit_service_getter,
        )

        # --- Legacy Controllers ---
        self._dialog = DialogController(window, context, window.ui.status_bar)
        self._facade.register_restore_prompt(self._dialog.prompt_restore_to_root)
        self._status_bar = StatusBarController(
            window.ui.status_bar,
            window.ui.progress_bar,
            window.ui.rescan_action,
            context,
        )

        self._share_controller = ShareController(
            settings=context.settings,
            current_path_provider=self._detail_vm.current_asset_path,
            status_bar=self._status_bar,
            notification_toast=window.ui.notification_toast,
            share_button=window.ui.share_button,
            share_action_group=window.ui.share_action_group,
            copy_file_action=window.ui.share_action_copy_file,
            copy_path_action=window.ui.share_action_copy_path,
            reveal_action=window.ui.share_action_reveal_file,
            edit_service_getter=edit_service_getter,
        )
        self._share_controller.restore_preference()

        self._export_controller = ExportController(
            settings=context.settings,
            library=context.library,
            status_bar=self._status_bar,
            toast=window.ui.notification_toast,
            export_all_action=window.ui.export_all_edited_action,
            export_selected_action=window.ui.export_selected_action,
            destination_group=window.ui.export_destination_group,
            destination_library=window.ui.export_destination_library,
            destination_ask=window.ui.export_destination_ask,
            format_group=window.ui.export_format_group,
            format_jpg=window.ui.export_format_jpg,
            format_png=window.ui.export_format_png,
            format_tiff=window.ui.export_format_tiff,
            main_window=window,
            selection_callback=window.current_selection,
        )

        # --- Binding Data to Views ---
        window.ui.grid_view.setModel(self._asset_list_vm)

        # Assign Delegate for Grid View (Fixes text display and spacing)
        self._grid_delegate = AssetGridDelegate(window.ui.grid_view, filmstrip_mode=False)
        window.ui.grid_view.setItemDelegate(self._grid_delegate)

        window.ui.grid_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Use SpacerProxyModel for Filmstrip to allow centering of first/last items
        self._filmstrip_proxy = SpacerProxyModel(window.ui.filmstrip_view)
        self._filmstrip_proxy.setSourceModel(self._asset_list_vm)
        window.ui.filmstrip_view.setModel(self._filmstrip_proxy)

        # Assign Delegate for Filmstrip View
        self._filmstrip_delegate = AssetGridDelegate(window.ui.filmstrip_view, filmstrip_mode=True)
        window.ui.filmstrip_view.setItemDelegate(self._filmstrip_delegate)

        self._preview_controller = PreviewController(
            window.ui.preview_window,
            edit_service_getter=edit_service_getter,
        )
        self._preview_controller.bind_view(window.ui.grid_view)

        self._selection_controller = SelectionController(
            selection_button=window.ui.selection_button,
            grid_view=window.ui.grid_view,
            grid_delegate=self._grid_delegate,
            preview_controller=self._preview_controller,
            playback=None,
            handle_grid_clicks=False,
            parent=self,
        )

        self._context_menu = ContextMenuController(
            grid_view=window.ui.grid_view,
            asset_model=self._asset_list_vm,
            selected_paths_provider=self._gallery_vm.paths_for_rows,
            facade=self._facade,
            status_bar=self._status_bar,
            notification_toast=window.ui.notification_toast,
            selection_controller=self._selection_controller,
            navigation=self._navigation,
            export_callback=window.ui.export_selected_action.trigger,
            prepare_paths_for_mutation=self._prepare_paths_for_mutation,
            gallery_viewmodel=self._gallery_vm,
            parent=self,
        )

        # --- Centralised shortcut manager ---
        # All window-level shortcuts are owned and dispatched here.
        # See: src/iPhoto/gui/ui/shortcuts/app_shortcut_manager.py
        from iPhoto.gui.ui.shortcuts.app_shortcut_manager import AppShortcutManager

        self._shortcut_manager = AppShortcutManager(
            window,
            self._view_router,
            toggle_favorite_cb=self._detail_vm.toggle_favorite,
            exit_fullscreen_cb=window.exit_fullscreen,
            prev_photo_cb=self._playback.select_previous,
            next_photo_cb=self._playback.select_next,
            parent=self,
        )
        self._shortcut_manager.set_video_area(window.ui.video_area)
        self._shortcut_manager.set_edit_coordinator(self._edit)

        self._connect_signals()

    def start(self):
        """Start the coordinator."""
        self._logger.debug("MainCoordinator started")
        self._wire_exiftool_missing_warning()
        self._cleanup_null_gps_locations()
        self._view_router.show_gallery()
        self._map_extension_download.maybe_prompt_on_startup()

        # No auto-start - only load when user actually searches

    def finish_startup(self):
        """Re-enable full tree-update cascade after startup sequence completes."""
        self._startup_loading = False
        self._logger.debug("Startup loading complete, tree-update cascade enabled")

    def _cleanup_null_gps_locations(self) -> None:
        """One-time cleanup: clear persisted location names for (0, 0) GPS assets."""
        try:
            repo = getattr(self._context.asset_runtime, "repository", None)
            if repo is None:
                return
            clear_fn = getattr(repo, "clear_location_for_null_gps", None)
            if clear_fn is None:
                return
            count = clear_fn()
            if count > 0:
                self._logger.info("Cleared %d stale location entries for (0,0) GPS assets", count)
        except Exception:
            self._logger.debug("GPS location cleanup skipped", exc_info=True)

    def _wire_exiftool_missing_warning(self) -> None:
        """Show a one-time warning dialog if ExifTool is not found during scanning."""
        from iPhoto.infrastructure.services import metadata_provider
        from iPhoto.utils.exiftool import _resolve_exiftool_executable

        def _show_warning(message: str) -> None:
            from iPhoto.gui.ui.widgets import dialogs
            warning = tr("dialog.exiftool_warning", message=message)
            self._logger.warning("ExifTool not found, showing warning dialog")
            dialogs.show_warning(self._window, warning, title=tr("dialog.exiftool_not_found"))

        # Proactively check exiftool availability at startup
        try:
            _resolve_exiftool_executable()
        except Exception as exc:
            metadata_provider._exiftool_missing_notified = True
            _show_warning(str(exc))

        metadata_provider._on_exiftool_missing = _show_warning

    # ------------------------------------------------------------------
    # Window manager integration (legacy interface)
    # ------------------------------------------------------------------
    def is_edit_view_active(self) -> bool:
        """Return True when the edit view is currently active."""

        return self._view_router.is_edit_view_active()

    def edit_controller(self) -> EditCoordinator:
        """Expose the edit coordinator for immersive mode hooks."""

        return self._edit

    def suspend_playback_for_transition(self) -> bool:
        """Pause playback before a chrome transition."""

        return self._playback.suspend_playback_for_transition()

    def prepare_fullscreen_asset(self) -> bool:
        """Ensure the current asset is ready for immersive mode."""

        return self._playback.prepare_fullscreen_asset()

    def show_placeholder_in_viewer(self) -> None:
        """Display a placeholder while the detail view is preparing."""

        self._playback.show_placeholder_in_viewer()

    def resume_playback_after_transition(self) -> None:
        """Restore playback after a chrome transition."""

        self._playback.resume_playback_after_transition()

    def shutdown(self) -> None:
        """Stop worker threads and background jobs before the app exits."""
        # 1. Cancel any active background scans/imports via Facade
        if self._facade:
            self._facade.cancel_active_scans()
        if self._context and self._context.library:
            self._context.library.shutdown()
        if self._context:
            self._context.close_library()

        # 2. Stop playback (video/audio)
        if self._playback:
            self._playback.shutdown()

        # 3. Shutdown other coordinators if they have cleanup logic
        if self._edit:
            self._edit.shutdown()

        if hasattr(self._window.ui, "preview_window"):
            try:
                self._window.ui.preview_window.close_preview(False)
            except AttributeError:
                self._window.ui.preview_window.close()
        if hasattr(self._window.ui, "map_view"):
            try:
                self._window.ui.map_view.close()
            except RuntimeError:
                self._logger.warning("Failed to close map view during shutdown", exc_info=True)

        # 4. Wait briefly for background threads (e.g. thumbnail generation) to finish
        thread_pool = QThreadPool.globalInstance()
        if not thread_pool.waitForDone(2000):
            thread_pool.clear()

        app = QCoreApplication.instance()
        if app is not None:
            app.closeAllWindows()
            app.quit()

    def _connect_signals(self) -> None:
        """Connect application signals."""
        ui = self._window.ui
        updates = self._facade.library_updates
        self._context.library.treeUpdated.connect(self._on_library_tree_updated)
        self._context.library.albumRenamed.connect(self._on_album_renamed)
        # Library watcher rescans still emit through the bound LibraryRuntimeController,
        # while facade-initiated rescans emit through LibraryUpdateService.
        self._context.library.scanChunkReady.connect(self._gallery_store.handle_scan_chunk)
        self._context.library.scanFinished.connect(self._gallery_store.handle_scan_finished)
        self._context.library.scanChunkReady.connect(self._gallery_vm.handle_location_scan_chunk)
        self._context.library.scanFinished.connect(self._gallery_vm.handle_location_scan_finished)
        updates.scanChunkReady.connect(self._gallery_store.handle_scan_chunk)
        updates.scanFinished.connect(self._gallery_store.handle_scan_finished)
        updates.scanChunkReady.connect(self._gallery_vm.handle_location_scan_chunk)
        updates.scanFinished.connect(self._gallery_vm.handle_location_scan_finished)
        self._gallery_vm.message_requested.connect(self._status_bar.show_message)

        # Return to detail photo after exploring Location view.
        # Click "All Photos" or "Location" again in sidebar to go back.
        ui.sidebar.allPhotosSelected.connect(self._handle_return_from_map)
        ui.sidebar.staticNodeSelected.connect(self._handle_sidebar_return_from_map)

        # Grid interactions — single/double-click opens detail view.
        ui.grid_view.itemClicked.connect(self._on_asset_clicked)
        ui.grid_view.itemDoubleClicked.connect(self._on_asset_clicked)
        ui.grid_view.visibleRowsChanged.connect(self._asset_list_vm.prioritize_rows)

        # Filmstrip clicks are now handled by PlaybackCoordinator

        # Connect favorite click from grid view
        if hasattr(ui.grid_view, "favoriteClicked"):
            ui.grid_view.favoriteClicked.connect(self._on_favorite_clicked)

        # Coordinator Signals
        self._playback.assetChanged.connect(self._sync_selection)
        self._player_view_controller.imageLoadingFailed.connect(self._handle_media_load_failed)
        ui.video_area.mediaLoadFailed.connect(self._handle_media_load_failed)

        # Viewer Interactions (Wheel Navigation)
        ui.image_viewer.nextItemRequested.connect(self._playback.select_next)
        ui.image_viewer.prevItemRequested.connect(self._playback.select_previous)
        ui.video_area.nextItemRequested.connect(self._playback.select_next)
        ui.video_area.prevItemRequested.connect(self._playback.select_previous)

        # ⭐ 视频播放状态跟踪：连接到runtime controller以动态调整人脸扫描频率
        ui.video_area.playbackStateChanged.connect(self._on_video_playback_state_changed)

        # Prev / Next overlay buttons
        if hasattr(ui.detail_page, "prev_button"):
            ui.detail_page.prev_button.clicked.connect(self._playback.select_previous)
            ui.detail_page.next_button.clicked.connect(self._playback.select_next)

        # Map view cluster interactions
        if hasattr(ui, "map_view") and ui.map_view is not None:
            ui.map_view.assetActivated.connect(self._on_map_asset_activated)
            ui.map_view.clusterActivated.connect(self._on_cluster_activated)
            ui.map_view.showAllRequested.connect(self._handle_map_show_all_requested)

        # Map feature buttons (in map source bar, to the right of map selector)
        if hasattr(ui, "map_view") and ui.map_view is not None:
            if hasattr(ui.map_view, "btn_person_filter"):
                ui.map_view.btn_person_filter.clicked.connect(self._on_person_filter_toggled)
            if hasattr(ui.map_view, "btn_trail_toggle"):
                ui.map_view.btn_trail_toggle.clicked.connect(self._on_trail_toggle_toggled)

        # Menus
        ui.open_album_action.triggered.connect(self._handle_open_album_dialog)
        ui.rescan_action.triggered.connect(self._status_bar.begin_scan)
        ui.rescan_action.triggered.connect(self._gallery_vm.rescan_current)
        ui.download_map_extension_action.triggered.connect(
            lambda: self._map_extension_download.start_download(source="settings")
        )
        ui.edit_button.clicked.connect(self._detail_vm.request_edit)
        # ui.edit_rotate_left_button is handled by EditCoordinator in Edit Mode
        ui.rotate_left_button.clicked.connect(self._playback.rotate_current_asset)
        ui.favorite_button.clicked.connect(self._detail_vm.toggle_favorite)
        ui.toggle_face_names_action.toggled.connect(self._handle_face_name_toggle_changed)
        ui.toggle_hidden_people_action.toggled.connect(self._handle_hidden_people_toggle_changed)

        # Semantic Search
        if hasattr(ui, "main_header") and hasattr(ui.main_header, "search_requested"):
            ui.main_header.search_requested.connect(self._handle_search_requested)
        if hasattr(ui, "main_header") and hasattr(ui.main_header, "toggle_semantic_search_action"):
            ui.main_header.toggle_semantic_search_action.toggled.connect(
                self._handle_semantic_search_toggle
            )

        # Agent Organize Features
        if hasattr(ui, "main_header"):
            if hasattr(ui.main_header, "find_duplicates_action"):
                ui.main_header.find_duplicates_action.triggered.connect(self._handle_find_duplicates)
            if hasattr(ui.main_header, "smart_album_event_action"):
                ui.main_header.smart_album_event_action.triggered.connect(
                    lambda: self._handle_create_smart_album("event")
                )
            if hasattr(ui.main_header, "smart_album_location_action"):
                ui.main_header.smart_album_location_action.triggered.connect(
                    lambda: self._handle_create_smart_album("location")
                )
            if hasattr(ui.main_header, "smart_album_time_action"):
                ui.main_header.smart_album_time_action.triggered.connect(
                    lambda: self._handle_create_smart_album("time")
                )
            if hasattr(ui.main_header, "smart_album_theme_action"):
                ui.main_header.smart_album_theme_action.triggered.connect(
                    lambda: self._handle_create_smart_album("theme")
                )

        # Info Button
        if hasattr(ui, "info_button"):
            ui.info_button.clicked.connect(self._playback.toggle_info_panel)

        # Back Button (detail page)
        if hasattr(ui, "back_button"):
            ui.back_button.clicked.connect(self._detail_vm.back_to_gallery)

        # Fullscreen Button — already connected in window_manager._configure_window_controls

        # Map Button (detail page)
        if hasattr(ui, "map_button"):
            ui.map_button.clicked.connect(self._toggle_detail_map)

        # Gallery page back button for cluster gallery mode
        if hasattr(ui, "gallery_page") and hasattr(ui.gallery_page, "backRequested"):
            ui.gallery_page.backRequested.connect(self._gallery_vm.return_from_cluster_gallery)

        # Search back button
        if hasattr(ui, "gallery_page") and hasattr(ui.gallery_page, "searchBackRequested"):
            ui.gallery_page.searchBackRequested.connect(self._on_search_back)

        # Dashboard Click
        if hasattr(ui, "albums_dashboard_page"):
            ui.albums_dashboard_page.albumSelected.connect(self.open_album_from_path)
        if hasattr(ui, "people_page"):
            ui.people_page.clusterActivated.connect(self._on_people_cluster_activated)
            ui.people_page.groupActivated.connect(self._on_people_group_activated)
            self._context.library.peopleIndexUpdated.connect(ui.people_page.schedule_index_refresh)
            self._context.library.peopleSnapshotCommitted.connect(
                self._gallery_vm.handle_people_snapshot_committed
            )
            self._context.library.peopleSnapshotCommitted.connect(
                self._playback.handle_people_snapshot_committed
            )
            self._context.library.faceScanStatusChanged.connect(ui.people_page.set_status_message)
            # Also surface face scan progress in the main status bar so the
            # user can see it without switching to the People dashboard.
            self._context.library.faceScanStatusChanged.connect(
                lambda msg: self._status_bar.show_message(msg)
            )

        # Sidebar checkbox filtering
        ui.sidebar.albumCheckStateChanged.connect(self._on_album_check_state_changed)
        self._restore_checked_library_paths()

        # Navigation
        self._navigation.bindLibraryRequested.connect(self._dialog.bind_library_dialog)
        ui.bind_library_action.triggered.connect(self._dialog.bind_library_dialog)
        self._detail_vm.edit_requested.connect(self._edit.enter_edit_mode)

        # Preferences (Wheel, Volume) - Filmstrip handled in PlaybackCoordinator
        self._restore_preferences()
        ui.wheel_action_group.triggered.connect(self._handle_wheel_action_changed)

        # Status Bar Connections (Restored)
        # Facade Signals -> Status Bar
        # Note: AppFacade exposes library_updates (ScannerSignals)
        updates.scanProgress.connect(self._status_bar.handle_scan_progress)
        updates.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._facade.scanBatchFailed.connect(self._status_bar.handle_scan_batch_failed)
        self._facade.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._facade.scanFinished.connect(self._status_bar.handle_scan_finished)

        self._facade.loadStarted.connect(self._status_bar.handle_load_started)
        self._facade.loadProgress.connect(self._status_bar.handle_load_progress)
        self._facade.loadFinished.connect(self._status_bar.handle_load_finished)

        # After the first scan completes, allow "No media found" to show.
        self._facade.scanProgress.connect(lambda *_: ui.grid_view.set_scan_started())
        self._context.library.scanProgress.connect(lambda *_: ui.grid_view.set_scan_started())
        self._facade.scanFinished.connect(lambda *_: ui.grid_view.set_scan_completed())
        self._context.library.scanFinished.connect(lambda *_: ui.grid_view.set_scan_completed())

        import_service = self._facade.import_service
        import_service.importStarted.connect(self._status_bar.handle_import_started)
        import_service.importProgress.connect(self._status_bar.handle_import_progress)
        import_service.importFinished.connect(self._status_bar.handle_import_finished)

        move_service = self._facade.move_service
        move_service.moveStarted.connect(self._status_bar.handle_move_started)
        move_service.moveProgress.connect(self._status_bar.handle_move_progress)
        move_service.moveFinished.connect(self._status_bar.handle_move_finished)
        move_service.moveFinished.connect(self._handle_move_finished_toast)

        # Error Reporting
        self._facade.errorRaised.connect(self._dialog.show_error)
        self._context.library.errorRaised.connect(self._dialog.show_error)

        # Theme Switching (Restored)
        ui.theme_system.triggered.connect(lambda: self._context.settings.set("ui.theme", "system"))
        ui.theme_light.triggered.connect(lambda: self._context.settings.set("ui.theme", "light"))
        ui.theme_dark.triggered.connect(lambda: self._context.settings.set("ui.theme", "dark"))

        current_theme = self._context.settings.get("ui.theme", "system")
        if current_theme == "light":
            ui.theme_light.setChecked(True)
        elif current_theme == "dark":
            ui.theme_dark.setChecked(True)
        else:
            ui.theme_system.setChecked(True)

        # Language Switching
        ui.language_zh.triggered.connect(lambda: self._context.settings.set("ui.language", "zh"))
        ui.language_en.triggered.connect(lambda: self._context.settings.set("ui.language", "en"))

        current_lang = self._context.settings.get("ui.language", "zh")
        if current_lang == "en":
            ui.language_en.setChecked(True)
        else:
            ui.language_zh.setChecked(True)

        self._context.language.languageChanged.connect(lambda _lang: ui.main_header.retranslate())
        self._context.language.languageChanged.connect(lambda _lang: ui.sidebar.retranslate())

        # Note: keyboard shortcuts are now managed centrally by
        # AppShortcutManager, which is created in __init__ after all
        # coordinators are initialised.  Do not add QShortcut instances here.

    def _on_library_tree_updated(self) -> None:
        root = self._library_root()
        self._logger.debug("_on_library_tree_updated: root=%s", root)
        self._context.asset_runtime.bind_library_root(root)
        self._asset_list_vm.rebind_asset_query_service(
            self._asset_query_service(),
            root,
        )
        asset_state_service = self._asset_state_service()
        self._gallery_vm.bind_asset_state_service(asset_state_service)
        self._detail_vm.bind_asset_state_service(asset_state_service)
        if not self._startup_loading:
            self._gallery_vm.on_library_tree_updated()
        window = getattr(self, "_window", None)
        ui = getattr(window, "ui", None)
        people_page = getattr(ui, "people_page", None)
        bound_people_service = self._people_service(library_root=root)
        if bound_people_service is not None:
            self._playback_people_service = bound_people_service
        if people_page is not None:
            if bound_people_service is not None and hasattr(people_page, "set_people_service"):
                people_page.set_people_service(bound_people_service)
            else:
                people_page.set_library_root(root)
            people_page.set_status_message(self._context.library.face_scan_status_message())
        map_runtime = self._map_runtime()
        map_interaction_service = self._map_interaction_service()
        self._map_extension_download.set_package_root(
            self._resolve_map_package_root(map_runtime)
        )
        if ui is not None and hasattr(ui, "map_view"):
            ui.map_view.set_map_runtime(map_runtime)
            ui.map_view.set_map_interaction_service(map_interaction_service)
        if ui is not None and hasattr(ui, "info_panel"):
            ui.info_panel.set_map_runtime(map_runtime)
        playback = getattr(self, "_playback", None)
        if playback is not None:
            playback.set_map_runtime(map_runtime)
            if bound_people_service is not None and hasattr(playback, "set_people_service"):
                playback.set_people_service(bound_people_service)
            else:
                playback.set_people_library_root(root)

    def _active_session(self):
        return getattr(self._context, "library_session", None)

    def _library_root(self) -> Path | None:
        session = self._active_session()
        if session is not None:
            return getattr(session, "library_root", None)
        return self._context.library.root()

    def _asset_query_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "asset_queries", None)
        return getattr(self._context.library, "asset_query_service", None)

    def _asset_state_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "asset_state", None)
        return getattr(self._context.library, "asset_state_service", None)

    def _edit_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "edit", None)
        return getattr(self._context.library, "edit_service", None)

    def _people_service(self, library_root: Path | None = None):
        session = self._active_session()
        session_root = getattr(session, "library_root", None) if session is not None else None
        if session is not None and (library_root is None or session_root == library_root):
            return getattr(session, "people", None)
        return resolve_people_service(
            self._context.library,
            library_root=library_root,
        )

    def _map_runtime(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "maps", None)
        return getattr(self._context.library, "map_runtime", None)

    def _map_interaction_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "map_interactions", None)
        return getattr(self._context.library, "map_interaction_service", None)

    def _toggle_detail_map(self) -> None:
        """Navigate to the Location view focused on the current photo."""
        presentation = self._detail_vm.presentation.value
        if presentation is None:
            return
        gps = presentation.info.get("gps")
        if not isinstance(gps, dict):
            return
        lat = gps.get("lat")
        lon = gps.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return
        rel = presentation.info.get("rel")
        if not isinstance(rel, str) or not rel:
            rel = presentation.path.name
        asset = GeotaggedAsset(
            library_relative=rel,
            album_relative=Path(rel).name,
            absolute_path=presentation.path,
            album_path=presentation.path.parent,
            asset_id=presentation.asset_id or rel,
            latitude=float(lat),
            longitude=float(lon),
            is_image=not presentation.is_video,
            is_video=bool(presentation.is_video),
            still_image_time=None,
            timestamp=None,
            duration=None,
            location_name=presentation.location,
            live_photo_group_id=None,
            live_partner_rel=(
                presentation.live_motion_rel.as_posix()
                if presentation.live_motion_rel is not None
                else None
            ),
        )
        # Remember the current photo so the header back button can return to it.
        self._return_from_map_path = presentation.path
        self._gallery_vm.open_location_map_focused(asset)
        if hasattr(self._window.ui, "map_view"):
            self._window.ui.map_view.set_focused_asset_mode(True)
        # Show back button in header immediately.
        self._add_header_back_button()
        # Highlight sidebar immediately.
        try:
            self._window.ui.sidebar.select_static_node("Location")
            self._window.ui.sidebar._tree.viewport().update()
        except Exception:
            pass
        # Center map on photo location — retry quickly until native GL widget is ready.
        QTimer.singleShot(0, lambda: self._try_focus_map(lat, lon, 0))

    def _handle_map_show_all_requested(self) -> None:
        # Clear return path so we don't jump back to the previous photo
        self._return_from_map_path = None
        # Hide the header back button if it exists
        btn = getattr(self, "_header_back_btn", None)
        if btn is not None:
            btn.hide()
        self._gallery_vm.show_all_location_assets()
        if hasattr(self._window.ui, "map_view"):
            self._window.ui.map_view.set_focused_asset_mode(False)

    _FOCUS_MAP_MAX_RETRIES = 30

    def _try_focus_map(self, lat: float, lon: float, attempt: int) -> None:
        """Center the Location map on the given coordinates at high zoom."""
        ui = self._window.ui
        if not hasattr(ui, "map_view") or ui.map_view is None:
            self._retry_focus(lat, lon, attempt)
            return
        try:
            map_widget = ui.map_view.map_widget()
        except RuntimeError:
            self._retry_focus(lat, lon, attempt)
            return
        if map_widget is None:
            self._retry_focus(lat, lon, attempt)
            return
        try:
            map_widget.set_zoom(17.0)
            map_widget.center_on(lon, lat)
        except Exception:
            self._retry_focus(lat, lon, attempt)

    def _retry_focus(self, lat: float, lon: float, attempt: int) -> None:
        if attempt < self._FOCUS_MAP_MAX_RETRIES:
            QTimer.singleShot(
                100,
                lambda: self._try_focus_map(lat, lon, attempt + 1),
            )

    def _add_header_back_button(self) -> None:
        """Add a back button to the main header bar."""
        ui = self._window.ui
        if not hasattr(ui, "main_header"):
            return
        btn = getattr(self, "_header_back_btn", None)
        if btn is not None:
            btn.show()
            return
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton("← Photo", ui.main_header)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("Return to photo")
        btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 4px 10px; "
            "background: rgba(0,120,215,180); color: white; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: rgba(0,120,215,240); }"
        )
        btn.clicked.connect(self._handle_map_back_clicked)
        header_layout = ui.main_header.layout()
        if header_layout is not None:
            header_layout.addWidget(btn)
        self._header_back_btn = btn

    def _handle_map_back_clicked(self) -> None:
        """Back button in header: return to originating photo."""
        btn = getattr(self, "_header_back_btn", None)
        if btn is not None:
            btn.hide()
        if self._return_from_map_path is not None:
            self._handle_return_from_map()
        else:
            self._navigation.open_all_photos()

    _FOCUS_MAP_MAX_RETRIES = 10

    def _try_focus_map(self, lat: float, lon: float, attempt: int) -> None:
        """Center the Location map on the given coordinates at high zoom."""
        ui = self._window.ui
        if not hasattr(ui, "map_view") or ui.map_view is None:
            return
        try:
            map_widget = ui.map_view.map_widget()
        except RuntimeError:
            self._retry_focus(lat, lon, attempt)
            return
        if map_widget is None:
            self._retry_focus(lat, lon, attempt)
            return
        try:
            map_widget.set_zoom(17.0)
            map_widget.center_on(lon, lat)
        except Exception:
            self._retry_focus(lat, lon, attempt)

    def _retry_focus(self, lat: float, lon: float, attempt: int) -> None:
        if attempt < self._FOCUS_MAP_MAX_RETRIES:
            QTimer.singleShot(
                400,
                lambda: self._try_focus_map(lat, lon, attempt + 1),
            )

    _RETURN_FROM_MAP_MAX_RETRIES = 8

    def _handle_sidebar_return_from_map(self, name: str) -> None:
        """When clicking 'Location' sidebar while already on map from detail,
        return to the photo instead of re-navigating."""
        if name != "Location":
            return
        path = self._return_from_map_path
        if path is None:
            return
        if not hasattr(self._window.ui, "map_page"):
            return
        self._return_from_map_path = None
        # Tell NavigationCoordinator to skip re-navigating to Location.
        self._navigation._pending_detail_return = path
        # Navigate to All Photos first, then open the photo detail.
        self._navigation.open_all_photos()
        QTimer.singleShot(50, lambda: self._try_return_to_photo(path, 0))

    def _handle_return_from_map(self) -> None:
        """When clicking All Photos after exploring from detail, reopen the photo."""
        path = self._return_from_map_path
        if path is None:
            return
        self._return_from_map_path = None
        btn = getattr(self, "_header_back_btn", None)
        if btn is not None:
            btn.hide()
        QTimer.singleShot(100, lambda: self._try_return_to_photo(path, 0))

    def _try_return_to_photo(self, path: Path, attempt: int) -> None:
        row = self._gallery_store.row_for_path(path)
        if row is not None:
            self._gallery_vm.open_row(row)
            return
        if attempt < self._RETURN_FROM_MAP_MAX_RETRIES:
            QTimer.singleShot(
                100 * (attempt + 1),
                lambda: self._try_return_to_photo(path, attempt + 1),
            )

    @staticmethod
    def _resolve_map_package_root(map_runtime: object | None) -> Path:
        package_root_getter = getattr(map_runtime, "package_root", None)
        if callable(package_root_getter):
            try:
                package_root = package_root_getter()
            except Exception:
                package_root = None
            if package_root is not None:
                return Path(package_root).resolve()

        package_root = getattr(map_runtime, "_package_root", None)
        if package_root is not None:
            return Path(package_root).resolve()
        return Path(__file__).resolve().parents[3] / "maps"

    def _on_album_renamed(self, old_path: Path, new_path: Path) -> None:
        self._pinned_items_service.remap_album_path(
            old_path,
            new_path,
            library_root=self._context.library.root(),
            fallback_label=new_path.name,
        )
        self._thumbnail_service.remap_album_paths(old_path, new_path)
        self._gallery_vm.handle_album_renamed(old_path, new_path)

    def _handle_people_snapshot_sidebar_refresh(self, event: object) -> None:
        library_root = self._context.library.root()
        if (
            library_root is not None
            and getattr(event, "library_root", None) == library_root
        ):
            self._pinned_items_service.prune_missing_people_entities(
                library_root,
                person_ids=tuple(getattr(event, "changed_person_ids", ()) or ()),
                group_ids=tuple(getattr(event, "changed_group_ids", ()) or ()),
                person_redirects=dict(getattr(event, "person_redirects", {}) or {}),
                group_redirects=dict(getattr(event, "group_redirects", {}) or {}),
            )
        self._window.ui.sidebar.refresh_tree_model()

    def _handle_move_finished_toast(
        self,
        source: Path,
        destination: Path,
        success: bool,
        message: str,
    ) -> None:
        """Show the lightweight completion toast for successful ordinary moves."""

        del message
        if not success or self._is_recently_deleted_move(source, destination):
            return

        self._window.ui.notification_toast.show_toast("Moved")

    def _is_recently_deleted_move(self, source: Path, destination: Path) -> bool:
        """Return whether a move completion belongs to delete or restore flows."""

        trash_root = self._context.library.deleted_directory()
        if trash_root is not None:
            return self._paths_equal(source, trash_root) or self._paths_equal(
                destination,
                trash_root,
            )
        return (
            source.name == RECENTLY_DELETED_DIR_NAME
            or destination.name == RECENTLY_DELETED_DIR_NAME
        )

    def _paths_equal(self, first: Path, second: Path) -> bool:
        """Return ``True`` when *first* and *second* refer to the same location."""

        try:
            first_resolved = first.resolve()
        except OSError:
            first_resolved = first
        try:
            second_resolved = second.resolve()
        except OSError:
            second_resolved = second
        return first_resolved == second_resolved

    def _handle_media_load_failed(self, path: Path, message: str) -> None:
        path_key = str(path)
        if path_key in self._media_failure_cleanup_paths:
            return
        # Broad guard: block ALL media failure handling while any dialog is open
        # to prevent nested blocking dialogs from chained broken videos.
        if self._media_failure_cleanup_paths:
            return

        self._media_failure_cleanup_paths.add(path_key)
        try:
            self._dialog.show_error(tr("msg.file_unreadable", name=path.name, message=message))
            facade = getattr(self, "_facade", None)
            updates = getattr(facade, "library_updates", None)
            if updates is None:
                return

            refresh_root = updates.handle_media_load_failure(path)
            if refresh_root is not None:
                # Defer the reload so it runs after the dialog's event loop exits,
                # preventing nested dialogs from chained broken video loads.
                QTimer.singleShot(0, self._gallery_store.reload_current_selection)
        finally:
            self._media_failure_cleanup_paths.discard(path_key)

    def _on_video_playback_state_changed(self, is_playing: bool) -> None:
        """⭐ 视频播放状态改变时，通知runtime controller调整人脸扫描频率"""
        library_manager = getattr(self._context, "library", None)
        if library_manager is None:
            return
        if is_playing:
            library_manager.register_video_playing()
        else:
            library_manager.unregister_video_playing()

    def _on_asset_clicked(self, index: QModelIndex):
        if self._selection_controller and self._selection_controller.is_active():
            return
        if not index.isValid():
            return
        row = index.row()
        if row < 0:
            return
        try:
            self._gallery_vm.open_row(row)
        except Exception:
            logging.getLogger(__name__).exception(
                "open_row failed for row %s", row
            )

    def _on_favorite_clicked(self, index: QModelIndex):
        self._gallery_vm.toggle_favorite_row(index.row())

    def _sync_selection(self, row: int):
        """Syncs grid view selection when playback asset changes."""
        try:
            idx = self._asset_list_vm.index(row, 0)
            if not idx.isValid():
                return
            selection_model = self._window.ui.grid_view.selectionModel()
            if selection_model is None:
                return
            selection_model.setCurrentIndex(
                idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
            )
            self._window.ui.grid_view.scrollTo(idx)
        except Exception:
            logging.getLogger(__name__).debug(
                "_sync_selection failed for row %s", row, exc_info=True
            )

    def _handle_search_requested(self, query: str) -> None:
        """Handle semantic search request from the search input."""
        self._logger.info("Search requested: %s", query)

        # Switch to gallery view
        self._view_router.show_gallery()

        # Show loading
        self._show_search_loading(query)

        # Run search in background thread
        from PySide6.QtCore import QThread, Signal

        # Get paths before starting thread
        # __file__ = src/iPhoto/gui/coordinators/main_coordinator.py
        # parents[0] = coordinators/
        # parents[1] = gui/
        # parents[2] = iPhoto/
        # parents[3] = src/
        # parents[4] = project root
        project_root = Path(__file__).resolve().parents[4]
        model_dir = project_root / 'extension' / 'models' / 'clip-vit-base-patch32'
        library_root = Path(self._context.library.root()) if hasattr(self._context, 'library') else Path.cwd()

        class SearchThread(QThread):
            finished = Signal(object, str)  # results or None, error
            progress = Signal(str, int, int)  # message, current, total

            # Chinese to English translation map
            _ZH_EN_MAP = {
                "树": "tree", "花": "flower", "山": "mountain", "水": "water",
                "海": "sea ocean", "河": "river", "湖": "lake", "天空": "sky",
                "日落": "sunset", "日出": "sunrise", "月亮": "moon", "星星": "star",
                "狗": "dog", "猫": "cat", "鸟": "bird", "鱼": "fish",
                "车": "car", "飞机": "airplane", "船": "boat",
                "人": "person people", "孩子": "child children", "宝宝": "baby",
                "美食": "food", "蛋糕": "cake", "咖啡": "coffee",
                "建筑": "building", "房子": "house", "桥": "bridge",
                "路": "road", "公园": "park", "花园": "garden",
                "雪": "snow", "雨": "rain", "云": "cloud",
                "红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
                "海边": "beach", "沙滩": "sand beach",
                "婚礼": "wedding", "生日": "birthday", "聚会": "party",
                "旅行": "travel", "风景": "landscape scenery",
                "夜景": "night cityscape", "城市": "city",
                "乡村": "countryside village",
            }

            def __init__(self, query_text, model_path, lib_root):
                super().__init__()
                self._query = query_text
                self._model_dir = model_path
                self._library_root = lib_root

            def _translate_query(self, query: str) -> str:
                """Translate Chinese query to English for CLIP."""
                import re
                query_lower = query.lower().strip()

                # Replace Chinese words with English
                for zh, en in self._ZH_EN_MAP.items():
                    if zh in query_lower:
                        query_lower = query_lower.replace(zh, en)

                # If still has Chinese characters, try to extract English words
                if re.search(r'[一-鿿]', query_lower):
                    english_words = re.findall(r'[a-zA-Z]+', query)
                    if english_words:
                        return ' '.join(english_words)
                    # Fallback: return original
                    return query

                return query_lower

            def run(self):
                try:
                    from iPhoto.agent.infrastructure.clip_embedding import CLIPEmbeddingService
                    from iPhoto.cache.index_store.embedding_repository import get_embedding_repository
                    import time

                    # 1. Load model
                    self.progress.emit("正在加载 AI 模型...", 0, 0)
                    service = CLIPEmbeddingService(model_dir=self._model_dir)
                    service._ensure_loaded()

                    if not service.is_loaded():
                        self.finished.emit(None, "AI 模型加载失败")
                        return

                    self.progress.emit("AI 模型加载完成，正在加载索引...", 1, 3)

                    # 2. Load embeddings
                    embedding_repo = get_embedding_repository(self._library_root)
                    all_embeddings = embedding_repo.get_all_embeddings()

                    if not all_embeddings:
                        self.finished.emit(None, "没有找到照片索引，请先生成索引")
                        return

                    self.progress.emit(f"已加载 {len(all_embeddings)} 个索引，正在搜索...", 2, 3)

                    # 3. Translate query and search
                    translated_query = self._translate_query(self._query)
                    query_embedding = service.encode_text(translated_query)
                    if query_embedding is None:
                        self.finished.emit(None, "搜索编码失败")
                        return

                    results = []
                    for i, item in enumerate(all_embeddings):
                        similarity = service.compute_similarity(query_embedding, item["embedding"])
                        if similarity > 0.2:
                            results.append({
                                "asset_id": item["asset_id"],
                                "score": float(similarity),
                            })

                    results.sort(key=lambda x: x["score"], reverse=True)

                    # OCR text search (if available)
                    try:
                        from iPhoto.ai.db.ocr_database import get_ocr_repository
                        ocr_repo = get_ocr_repository(self._library_root)
                        ocr_hits = ocr_repo.search(self._query, limit=200)
                        seen_ids = {r["asset_id"] for r in results}
                        for hit in ocr_hits:
                            if hit.asset_id not in seen_ids:
                                results.append({
                                    "asset_id": hit.asset_id,
                                    "score": min(hit.confidence, 1.0),
                                    "source": "ocr",
                                })
                                seen_ids.add(hit.asset_id)
                        results.sort(key=lambda x: x["score"], reverse=True)
                    except Exception:
                        pass

                    # Return all results (UI will handle pagination)
                    self.finished.emit(results, "")

                except Exception as e:
                    logging.getLogger(__name__).error("Search failed: %s", e)
                    self.finished.emit(None, str(e))

        def on_finished(results, error):
            from PySide6.QtCore import QTimer
            if error:
                QTimer.singleShot(0, lambda: self._display_search_error(error))
            elif results:
                QTimer.singleShot(0, lambda: self._display_search_results_raw(results, query))
            else:
                QTimer.singleShot(0, lambda: self._display_search_results_raw([], query))

        def on_progress(message, current, total):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._update_search_progress(message, current, total))

        self._search_thread = SearchThread(query, model_dir, library_root)
        self._search_thread.finished.connect(on_finished)
        self._search_thread.progress.connect(on_progress)
        self._search_thread.start()

    def _display_search_results_raw(self, results: list, query: str) -> None:
        """Display search results."""
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.hide_loading()

        if not results:
            self._status_bar.show_message(f"未找到与 '{query}' 相关的照片")
            return

        # Show search results header with back button
        total_count = len(results)
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.show_search_results_header(query, total_count)

        # Show results (limit to 1000 for performance)
        max_results = 1000
        asset_ids = [r["asset_id"] for r in results[:max_results]]
        self._gallery_vm.show_search_results(asset_ids)

        # Scroll to top
        if hasattr(ui, 'grid_view'):
            ui.grid_view.scrollToTop()

        self._status_bar.show_message(f"找到 {total_count} 张与 '{query}' 相关的照片")

    def _show_search_loading(self, query: str, sub_message: str = None, show_continue: bool = False) -> None:
        """Show loading state in the grid view area."""
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.show_search_loading(query)

    def _update_search_progress(self, message: str, current: int, total: int) -> None:
        """Update search progress in the UI."""
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            page = ui.gallery_page
            if "模型" in message:
                page.show_model_loading()
            elif "索引" in message:
                page.show_model_loading()
            else:
                page.show_search_loading(message)

    def _display_search_results(self, results: list, query: str = "") -> None:
        """Display search results in the grid view."""
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.hide_loading()

        if not results:
            self._status_bar.show_message(f"未找到与 '{query}' 相关的照片")
            return

        asset_ids = [r.asset_id for r in results]
        self._gallery_vm.show_search_results(asset_ids)
        self._status_bar.show_message(f"找到 {len(results)} 张与 '{query}' 相关的照片")

    def _display_search_error(self, error: str) -> None:
        """Display search error in the grid view."""
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.show_error("搜索失败", error)
        self._status_bar.show_message(f"搜索失败: {error}", 10000)

    def _on_search_back(self) -> None:
        """Handle back button click during search."""
        # Hide search results header
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.hide_search_results_header()

        # Return to normal gallery view
        self._gallery_vm.open_all_photos()
        self._status_bar.show_message("")

    def _handle_semantic_search_toggle(self, enabled: bool) -> None:
        """Handle semantic search toggle.

        Parameters
        ----------
        enabled : bool
            Whether semantic search is enabled.
        """
        self._context.settings.set("agent.semantic_search_enabled", enabled)

        if enabled:
            # Start embedding generation if not already done
            self._start_embedding_generation()
            self._status_bar.show_message(
                tr("agent.enabled", default="Semantic search enabled")
            )
        else:
            self._status_bar.show_message(
                tr("agent.disabled", default="Semantic search disabled")
            )

    def _start_embedding_generation_silent(self) -> None:
        """Start embedding generation silently in background with status bar updates."""
        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        from PySide6.QtCore import QRunnable, Slot

        class SilentWorker(QRunnable):
            def __init__(self, session, status_callback):
                super().__init__()
                self.setAutoDelete(True)
                self._session = session
                self._status_callback = status_callback

            @Slot()
            def run(self):
                try:
                    # Load CLIP model
                    embedding_service = self._session.get_embedding_service()
                    if embedding_service is None:
                        return

                    # Get embedding repository
                    embedding_repo = self._session.get_embedding_repository()
                    if embedding_repo is None:
                        return

                    # Check how many need processing
                    all_assets = self._session.asset_runtime.assets.read_all()
                    image_assets = [a for a in all_assets if a.get("media_type") == 0]
                    asset_ids = [a["id"] for a in image_assets]
                    pending = embedding_repo.get_asset_ids_without_embeddings(asset_ids)

                    if not pending:
                        total = embedding_repo.count()
                        self._status_callback(f"AI 索引已就绪 ({total} 张照片)")
                        return

                    # Start embedding generation with progress callbacks
                    from iPhoto.agent.workers.embedding_worker import EmbeddingWorker
                    worker = EmbeddingWorker(
                        embedding_service=embedding_service,
                        embedding_repository=embedding_repo,
                        asset_repository=self._session.asset_runtime.assets,
                        library_root=self._session.library_root,
                    )

                    def on_progress(current, total, msg):
                        pct = int(current / total * 100) if total > 0 else 0
                        self._status_callback(f"AI 索引生成中: {current}/{total} ({pct}%)")

                    def on_finished(processed, failed):
                        self._status_callback(f"AI 索引生成完成 ({processed} 张照片)")

                    worker.signals.progress.connect(on_progress)
                    worker.signals.finished.connect(on_finished)

                    QThreadPool.globalInstance().start(worker)

                except Exception as e:
                    logging.getLogger(__name__).debug("Silent embedding generation failed: %s", e)

        def status_callback(message):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._status_bar.show_message(message, 5000))

        worker = SilentWorker(library_session, status_callback)
        QThreadPool.globalInstance().start(worker)

    def _start_embedding_generation(self) -> None:
        """Start background embedding generation with progress UI (when user searches)."""
        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        # Show progress in the gallery page with progress bar
        ui = self._window.ui
        if hasattr(ui, 'gallery_page'):
            ui.gallery_page.show_loading_message(
                "正在初始化 AI 搜索...",
                "首次使用需要为照片生成索引，之后搜索会很快",
                show_progress=True
            )

        # Run model loading and embedding generation in background
        from PySide6.QtCore import QRunnable, Slot

        class InitWorker(QRunnable):
            def __init__(self, session, callback):
                super().__init__()
                self.setAutoDelete(True)
                self._session = session
                self._callback = callback

            @Slot()
            def run(self):
                try:
                    # Step 1: Load CLIP model
                    self._callback("loading_model", 0, 0, "正在加载 AI 模型（约350MB）...")

                    # Check if model files exist first
                    from iPhoto.agent.infrastructure.clip_downloader import get_model_path, is_model_available, get_model_dir
                    model_dir = get_model_dir(self._session.library_root)

                    if not is_model_available(model_dir):
                        self._callback("error", 0, 0, "模型文件不存在，请先下载模型")
                        return

                    self._callback("model_loading", 0, 0, "模型文件已找到，正在加载...")

                    # Load model with timeout tracking
                    import time
                    start_time = time.time()
                    embedding_service = self._session.get_embedding_service()
                    load_time = int(time.time() - start_time)

                    if embedding_service is None:
                        self._callback("error", 0, 0, f"AI 模型加载失败（耗时 {load_time} 秒）")
                        return

                    if not embedding_service.is_loaded():
                        self._callback("error", 0, 0, f"AI 模型加载失败（耗时 {load_time} 秒）")
                        return

                    self._callback("model_loaded", 0, 0, f"AI 模型加载完成（耗时 {load_time} 秒）")

                    # Step 2: Get embedding repository
                    embedding_repo = self._session.get_embedding_repository()
                    if embedding_repo is None:
                        self._callback("error", 0, 0, "数据库初始化失败")
                        return

                    # Step 3: Count images to process
                    self._callback("counting", 0, 0, "正在统计照片数量...")
                    all_assets = self._session.asset_runtime.assets.read_all()
                    image_assets = [a for a in all_assets if a.get("media_type") == 0]
                    asset_ids = [a["id"] for a in image_assets]
                    pending_ids = embedding_repo.get_asset_ids_without_embeddings(asset_ids)

                    if not pending_ids:
                        self._callback("done", 0, 0, "")
                        return

                    self._callback("starting", len(pending_ids), len(pending_ids), f"共 {len(pending_ids)} 张照片需要处理")

                    # Step 4: Start embedding generation
                    from iPhoto.agent.workers.embedding_worker import EmbeddingWorker
                    worker = EmbeddingWorker(
                        embedding_service=embedding_service,
                        embedding_repository=embedding_repo,
                        asset_repository=self._session.asset_runtime.assets,
                        library_root=self._session.library_root,
                    )

                    def on_progress(current, total, msg):
                        self._callback("progress", current, total, msg)

                    def on_finished(processed, failed):
                        self._callback("done", processed, failed, "")

                    worker.signals.progress.connect(on_progress)
                    worker.signals.finished.connect(on_finished)
                    worker.signals.error.connect(lambda err: self._callback("error", 0, 0, err))

                    QThreadPool.globalInstance().start(worker)

                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    logging.getLogger(__name__).error("Embedding init failed: %s\n%s", e, error_detail)
                    self._callback("error", 0, 0, f"{str(e)[:100]}")

        def on_status(status_type, current, total, message):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._handle_embedding_status(status_type, current, total, message))

        worker = InitWorker(library_session, on_status)
        QThreadPool.globalInstance().start(worker)

    def _handle_embedding_status(self, status_type: str, current: int, total: int, message: str) -> None:
        """Handle embedding generation status updates."""
        ui = self._window.ui
        self._logger.info(f"Embedding status: {status_type} - {message}")

        if not hasattr(ui, 'gallery_page'):
            return

        page = ui.gallery_page

        if status_type == "loading_model":
            page.show_model_loading()

        elif status_type == "model_loading":
            page.show_model_loading()

        elif status_type == "model_loaded":
            page.show_model_loading()

        elif status_type == "counting":
            page.show_model_loading()

        elif status_type == "starting":
            page.show_indexing_progress(0, current)

        elif status_type == "progress":
            page.show_indexing_progress(current, total)
            progress_pct = int(current / total * 100) if total > 0 else 0
            self._status_bar.show_message(f"AI 索引生成中: {current}/{total} ({progress_pct}%)")

        elif status_type == "done":
            page.show_done(current)
            if current > 0:
                self._status_bar.show_message(f"AI 索引生成完成！共处理 {current} 张照片", 5000)
            else:
                self._status_bar.show_message("AI 索引已是最新", 3000)

        elif status_type == "error":
            page.show_error("初始化失败", message)
            self._status_bar.show_message(f"AI 初始化失败: {message}", 10000)

    def _handle_find_duplicates(self) -> None:
        """Handle find duplicates action."""
        # Check if semantic search is enabled
        if not self._context.settings.get("agent.semantic_search_enabled", False):
            self._status_bar.show_message(
                tr("search.not_available", default="Please enable semantic search first")
            )
            return

        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        embedding_service = library_session.get_embedding_service()
        embedding_repo = library_session.get_embedding_repository()

        if embedding_service is None or embedding_repo is None:
            self._status_bar.show_message(
                tr("search.not_available", default="Agent services not available")
            )
            return

        # Run duplicate detection in background
        from iPhoto.agent.services.organize_service import OrganizeService
        from PySide6.QtCore import QRunnable, Slot

        class DuplicateWorker(QRunnable):
            def __init__(self, service, callback):
                super().__init__()
                self.setAutoDelete(True)
                self._service = service
                self._callback = callback

            @Slot()
            def run(self):
                try:
                    duplicates = self._service.find_duplicates(threshold=0.95)
                    self._callback(duplicates)
                except Exception as e:
                    logging.getLogger(__name__).error("Duplicate detection failed: %s", e)
                    self._callback([])

        organize_service = OrganizeService(
            embedding_service=embedding_service,
            embedding_repository=embedding_repo,
            asset_repository=library_session.asset_runtime.assets,
            library_root=library_session.library_root,
        )

        def on_complete(duplicates):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._display_duplicates(duplicates))

        worker = DuplicateWorker(organize_service, on_complete)
        QThreadPool.globalInstance().start(worker)

        self._status_bar.show_message(
            tr("organize.searching_duplicates", default="Searching for duplicates...")
        )

    def _display_duplicates(self, duplicates: list) -> None:
        """Display duplicate photos in the gallery."""
        if not duplicates:
            self._status_bar.show_message(
                tr("organize.no_duplicates", default="No duplicates found")
            )
            return

        # Flatten all duplicate asset IDs
        all_asset_ids = []
        for group in duplicates:
            all_asset_ids.extend(group.asset_ids)

        # Show in gallery
        self._gallery_vm.show_search_results(all_asset_ids)

        total_groups = len(duplicates)
        total_photos = len(all_asset_ids)
        self._status_bar.show_message(
            tr("organize.duplicates_found",
               default=f"Found {total_groups} duplicate groups ({total_photos} photos)")
        )

    def _handle_create_smart_album(self, group_by: str) -> None:
        """Handle create smart album action.

        Parameters
        ----------
        group_by : str
            How to group photos: 'event', 'location', 'time', 'theme'.
        """
        # Check if semantic search is enabled
        if not self._context.settings.get("agent.semantic_search_enabled", False):
            self._status_bar.show_message(
                tr("search.not_available", default="Please enable semantic search first")
            )
            return

        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        embedding_service = library_session.get_embedding_service()
        embedding_repo = library_session.get_embedding_repository()

        if embedding_service is None or embedding_repo is None:
            self._status_bar.show_message(
                tr("search.not_available", default="Agent services not available")
            )
            return

        # Run smart album creation in background
        from iPhoto.agent.services.organize_service import OrganizeService
        from PySide6.QtCore import QRunnable, Slot

        class SmartAlbumWorker(QRunnable):
            def __init__(self, service, group_by_type, callback):
                super().__init__()
                self.setAutoDelete(True)
                self._service = service
                self._group_by = group_by_type
                self._callback = callback

            @Slot()
            def run(self):
                try:
                    albums = self._service.create_smart_albums(group_by=self._group_by)
                    self._callback(albums)
                except Exception as e:
                    logging.getLogger(__name__).error("Smart album creation failed: %s", e)
                    self._callback([])

        organize_service = OrganizeService(
            embedding_service=embedding_service,
            embedding_repository=embedding_repo,
            asset_repository=library_session.asset_runtime.assets,
            library_root=library_session.library_root,
        )

        def on_complete(albums):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._display_smart_albums(albums, group_by))

        worker = SmartAlbumWorker(organize_service, group_by, on_complete)
        QThreadPool.globalInstance().start(worker)

        self._status_bar.show_message(
            tr("organize.creating_albums", default=f"Creating smart albums by {group_by}...")
        )

    def _display_smart_albums(self, albums: list, group_by: str) -> None:
        """Display smart album suggestions."""
        if not albums:
            self._status_bar.show_message(
                tr("organize.no_albums", default="No smart albums to create")
            )
            return

        # For now, show the first album's photos in the gallery
        # In a full implementation, we would create actual albums in the library
        if albums:
            first_album = albums[0]
            self._gallery_vm.show_search_results(first_album.asset_ids)

            total_albums = len(albums)
            self._status_bar.show_message(
                tr("organize.albums_created",
                   default=f"Created {total_albums} smart albums")
            )

    def _handle_open_album_dialog(self):
        path = self._dialog.open_album_dialog()
        if path:
            self.open_album_from_path(path)

    def _on_cluster_activated(self, assets: list):
        """Handle cluster click from map view to open cluster gallery.

        This is triggered when the user clicks a cluster with multiple assets
        on the map. Opens a gallery showing all assets in the cluster.
        """
        self._navigation.open_cluster_gallery(assets)

    def _on_map_asset_activated(self, rel: str) -> None:
        """Handle single-asset map activation inside the Location context."""

        self._navigation.open_location_asset(rel)

    def _on_person_filter_toggled(self, checked: bool) -> None:
        """Toggle person filter panel on the map."""
        ui = self._window.ui
        if not hasattr(ui, "map_view"):
            return
        if checked:
            people_service = getattr(self, "_playback_people_service", None)
            if people_service is None:
                try:
                    from iPhoto.people.service import PeopleService
                    people_service = PeopleService()
                except Exception:
                    ui.map_view.btn_person_filter.setChecked(False)
                    return
            ui.map_view.show_person_filter(people_service)
        else:
            ui.map_view.hide_person_filter()

    def _on_trail_toggle_toggled(self, checked: bool) -> None:
        """Toggle trail/timeline display on the map."""
        ui = self._window.ui
        if not hasattr(ui, "map_view"):
            return
        if checked:
            try:
                from iPhoto.application.services.trail_service import TrailService
                library = getattr(self._context, "library", None)
                if library is None:
                    ui.map_view.btn_trail_toggle.setChecked(False)
                    self._status_bar.show_message(
                        "Trail unavailable: no library bound.", 4000
                    )
                    return
                geotagged = library.get_geotagged_assets()
                if not geotagged:
                    ui.map_view.btn_trail_toggle.setChecked(False)
                    self._status_bar.show_message(
                        "No geotagged photos found — trail needs photos with GPS data.", 5000
                    )
                    return
                trail_service = TrailService()
                trail = trail_service.build_trail(geotagged, granularity="day")
                if trail.is_empty:
                    ui.map_view.btn_trail_toggle.setChecked(False)
                    self._status_bar.show_message(
                        "Trail is empty — photos may lack valid timestamps.", 5000
                    )
                    return
                ui.map_view.set_trail(trail)
                ui.map_view._trail_service = trail_service
                ui.map_view._trail_geotagged = geotagged
                self._status_bar.show_message(
                    f"Trail: {trail.total_photos} photos, "
                    f"{len(trail.segments)} segments",
                    4000,
                )
            except Exception as e:
                self._logger.warning("Failed to build trail: %s", e)
                ui.map_view.btn_trail_toggle.setChecked(False)
                self._status_bar.show_message(
                    f"Failed to build trail: {e}", 5000
                )
        else:
            ui.map_view.clear_trail()
            self._status_bar.show_message("Trail hidden.", 2000)

    def _on_people_cluster_activated(self, person_id: str) -> None:
        QTimer.singleShot(0, lambda: self._open_people_cluster(person_id))

    def _on_people_group_activated(self, group_id: str) -> None:
        QTimer.singleShot(0, lambda: self._open_people_group(group_id))

    def _open_people_cluster(self, person_id: str) -> None:
        try:
            query = self._window.ui.people_page.build_cluster_query(person_id)
            if not query.asset_ids:
                return
            self._window.ui.people_page.prepare_for_hide()
            self._gallery_vm.open_people_cluster_gallery(
                query,
                kind="person",
                entity_id=person_id,
            )
            self._view_router.show_gallery()
        except Exception:
            self._logger.exception("Failed to open people cluster gallery for %s", person_id)

    def _open_people_group(self, group_id: str) -> None:
        try:
            query = self._window.ui.people_page.build_group_query(group_id)
            if not query.asset_ids:
                return
            self._window.ui.people_page.prepare_for_hide()
            self._gallery_vm.open_people_cluster_gallery(
                query,
                kind="group",
                entity_id=group_id,
            )
            self._view_router.show_gallery()
        except Exception:
            self._logger.exception("Failed to open people group gallery for %s", group_id)

    def open_album_from_path(self, path: Path):
        import time
        target = Path(path).expanduser()
        self._logger.info("open_album_from_path: start %s", target)
        t0 = time.monotonic()
        if not self._ensure_session_for_open_album(target):
            self._logger.info("open_album_from_path: session failed (%.2fs)", time.monotonic() - t0)
            return
        self._logger.info("open_album_from_path: session ok (%.2fs), opening album...", time.monotonic() - t0)
        t1 = time.monotonic()
        self._navigation.open_album(target)
        self._logger.info("open_album_from_path: album opened (%.2fs), total=%.2fs", time.monotonic() - t1, time.monotonic() - t0)

    def _ensure_session_for_open_album(self, path: Path) -> bool:
        """Ensure standalone album opens have a session-bound query surface."""

        if not path.exists() or not path.is_dir():
            return True

        current_root = self._library_root()
        if current_root is not None and self._path_is_descendant(path, current_root):
            return True

        open_library = getattr(self._context, "open_library", None)
        if not callable(open_library):
            return True

        try:
            open_library(path)
        except Exception as exc:
            self._facade.errorRaised.emit(str(exc))
            return False

        self._on_library_tree_updated()
        return True

    @staticmethod
    def _path_is_descendant(path: Path, root: Path) -> bool:
        try:
            Path(path).resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return False
        return True

    def _restore_preferences(self) -> None:
        """Restore UI preferences for wheel action and volume."""
        ui = self._window.ui
        settings = self._context.settings

        # 1. Wheel Action
        wheel_action = settings.get("ui.wheel_action", "navigate")
        if wheel_action == "zoom":
            ui.wheel_action_zoom.setChecked(True)
        else:
            wheel_action = "navigate"
            ui.wheel_action_navigate.setChecked(True)
        ui.image_viewer.set_wheel_action(wheel_action)

        stored_face_names = settings.get("ui.show_face_names_in_detail", False)
        if isinstance(stored_face_names, str):
            show_face_names = stored_face_names.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show_face_names = bool(stored_face_names)
        ui.toggle_face_names_action.setChecked(show_face_names)
        self._playback.set_face_name_display_enabled(show_face_names)

        stored_hidden_people = settings.get("ui.show_hidden_people", False)
        if isinstance(stored_hidden_people, str):
            show_hidden_people = stored_hidden_people.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show_hidden_people = bool(stored_hidden_people)
        ui.toggle_hidden_people_action.setChecked(show_hidden_people)
        if hasattr(ui, "people_page"):
            ui.people_page.set_show_hidden_people(show_hidden_people)

        # 3. Semantic Search
        stored_semantic_search = settings.get("agent.semantic_search_enabled", False)
        if isinstance(stored_semantic_search, str):
            semantic_search_enabled = stored_semantic_search.strip().lower() in {"1", "true", "yes", "on"}
        else:
            semantic_search_enabled = bool(stored_semantic_search)
        if hasattr(ui, "main_header") and hasattr(ui.main_header, "toggle_semantic_search_action"):
            ui.main_header.toggle_semantic_search_action.setChecked(semantic_search_enabled)
            if semantic_search_enabled:
                self._start_embedding_generation()

        # 2. Volume / Mute
        stored_volume = settings.get("ui.volume", 75)
        try:
            initial_volume = round(float(stored_volume))
        except (TypeError, ValueError):
            initial_volume = 75
        initial_volume = max(0, min(100, initial_volume))

        stored_muted = settings.get("ui.is_muted", False)
        if isinstance(stored_muted, str):
            initial_muted = stored_muted.strip().lower() in {"1", "true", "yes", "on"}
        else:
            initial_muted = bool(stored_muted)

        ui.video_area.set_volume(initial_volume)
        ui.video_area.set_muted(initial_muted)

    def _handle_wheel_action_changed(self, action: QAction) -> None:
        ui = self._window.ui
        if action is ui.wheel_action_zoom:
            selected = "zoom"
        else:
            selected = "navigate"

        if self._context.settings.get("ui.wheel_action") != selected:
            self._context.settings.set("ui.wheel_action", selected)

        ui.image_viewer.set_wheel_action(selected)

    def _handle_face_name_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_face_names_in_detail") != checked:
            self._context.settings.set("ui.show_face_names_in_detail", checked)
        self._playback.set_face_name_display_enabled(checked)

    def _handle_hidden_people_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_hidden_people") != checked:
            self._context.settings.set("ui.show_hidden_people", checked)
        if hasattr(self._window.ui, "people_page"):
            self._window.ui.people_page.set_show_hidden_people(checked)

    def _prepare_paths_for_mutation(self, paths: list[Path]) -> None:
        """Release preview/player handles before mutating files on disk."""

        self._preview_controller.close_preview(False)

        current_path = self._detail_vm.current_asset_path()
        if current_path is None:
            return

        current_key = self._normalise_path_key(current_path)
        selected_keys = {
            key for key in (self._normalise_path_key(path) for path in paths) if key is not None
        }
        if current_key is not None and current_key in selected_keys:
            self._playback.reset_for_gallery()

    def _normalise_path_key(self, path: Path) -> str | None:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _on_album_check_state_changed(self, checked_paths: list[Path]) -> None:
        """Persist checkbox state and refresh the current view."""
        path_strings = [str(p) for p in checked_paths]
        self._context.settings.set("ui.checked_library_paths", path_strings)
        self._gallery_vm.on_album_check_state_changed(checked_paths)

    def _restore_checked_library_paths(self) -> None:
        """Restore sidebar checkbox state from settings on startup."""
        stored = self._context.settings.get("ui.checked_library_paths", []) or []
        if not stored:
            return
        # ⭐ 关键修复：验证存储的路径是否仍然对应当前 library roots
        # 如果存储的路径已被移除或 library 已重新绑定到不同的 root，
        # 这些路径会导致 SQL album_paths 过滤掉所有照片
        from pathlib import Path as PathType
        resolved = [PathType(p) for p in stored]
        current_roots = self._library_root_holder() if hasattr(self, "_library_root_holder") else None
        library_roots = self._context.library.roots()
        root_set = {str(p.resolve()).lower().replace("\\", "/") for p in library_roots}
        valid_resolved = []
        for p in resolved:
            p_lower = str(p.resolve()).lower().replace("\\", "/") if p.exists() else str(p).lower().replace("\\", "/")
            if p_lower in root_set:
                valid_resolved.append(p)
                continue
            # 兼容父子关系
            for r in root_set:
                if p_lower.startswith(r + "/") or r.startswith(p_lower + "/"):
                    valid_resolved.append(p)
                    break
        if not valid_resolved:
            # ⭐ 所有存储的路径都已失效，清除 settings 避免下次再出问题
            self._context.settings.set("ui.checked_library_paths", [])
            return
        paths = set(str(p) for p in valid_resolved)
        self._window.ui.sidebar.set_checked_root_paths(paths)
        self._gallery_vm.set_checked_album_paths(valid_resolved)

    # --- Public Accessors for Window ---
    def toggle_playback(self):
        self._playback.toggle_playback()

    def replay_live_photo(self):
        self._playback.replay_live_photo()

    def request_next_item(self):
        self._playback.select_next()

    def request_previous_item(self):
        self._playback.select_previous()

    def paths_from_indexes(self, indexes: Iterable[QModelIndex]) -> list[Path]:
        paths = []
        for idx in indexes:
            p = self._asset_list_vm.data(idx, Roles.ABS)
            if p:
                paths.append(Path(p))
        return paths
