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
    QSize,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import QAction

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.config import SearchSettings
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.i18n import tr
from iPhoto.application.dtos import ScanStatus
from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.services.embedding_bootstrap import (
    EmbeddingIndexWorker,
    EmbeddingInitWorker,
    SearchModelDownloadWorker,
)
from iPhoto.gui.services.search_thread import (
    SearchThread,
    clear_search_embedding_cache,
)
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
from ...utils.env import is_truthy
from ...utils.pathutils import paths_equal

if TYPE_CHECKING:
    from iPhoto.gui.ui.main_window import MainWindow


#: The map widget is created lazily after the Location page is first shown, so
#: focusing waits for it. Values are policy, not implementation detail.
MAP_FOCUS_MAX_RETRIES = 10
MAP_FOCUS_RETRY_INTERVAL_MS = 400


#: Search workers that were still running when the app shut down. Keeping them
#: referenced prevents a live QThread from being destroyed during teardown.
_ORPHANED_SEARCH_THREADS: list = []

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
            on_model_populated=window.ui.grid_view.dismiss_loading_state,
            library_root=lib_root,
            parent=window.ui.grid_view,
        )
        self._gallery_store = self._asset_list_vm.store
        self._media_session = MediaSelectionSession()
        self._media_session.bind_collection(self._gallery_store)
        # Active semantic search: query, results, completion state and a serial so
        # a superseded search cannot overwrite the newer one.
        self._search_state: dict | None = None
        self._search_serial = 0
        # Running search threads. The previous one must stay referenced until it
        # finishes: replacing the only reference destroyed a live QThread.
        self._search_threads: list = []
        self._thumbnail_service = self._context.asset_runtime.thumbnail_service
        bound_people_service = self._people_service(library_root=lib_root)
        self._playback_people_service = bound_people_service or PeopleService()
        if bound_people_service is not None:
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
        window.ui.people_page.set_pinned_service(self._pinned_items_service)
        window.ui.albums_dashboard_page.set_pinned_service(self._pinned_items_service)
        self._facade.albumCoverUpdated.connect(
            window.ui.albums_dashboard_page.update_album_cover
        )
        # Inject ViewModel provider into Facade for legacy operations (restore/delete)
        if self._facade:
            self._facade.set_model_provider(lambda: self._asset_list_vm)

        # The playback coordinator reads AI captions/tags from the active library
        # session, which only exists once an album opens.
        self._session = None

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
            library_session_getter=lambda: self._session,
            map_runtime=self._map_runtime(),
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        self._navigation.set_playback_coordinator(self._playback)
        context.library.peopleSnapshotCommitted.connect(
            self._handle_people_snapshot_sidebar_refresh
        )
        # Manually attach info panel if available
        window.ui.info_panel.set_map_runtime(self._map_runtime())
        self._playback.set_info_panel(window.ui.info_panel)
        window.ui.info_panel.downloadMapExtensionRequested.connect(
            lambda: self._map_extension_download.start_download(source="info_panel")
        )
        window.ui.map_view.set_map_runtime(self._map_runtime())
        window.ui.map_view.set_map_interaction_service(
            self._map_interaction_service()
        )
        # Detail map panel (kept for GPS coordinates display only).
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
        # 0. Search workers load the AI model; let them finish or expire before the
        #    window (and their Qt parent objects) are torn down.
        for thread in list(getattr(self, "_search_threads", [])):
            if thread.isRunning():
                thread.requestInterruption()
                thread.wait(3000)
            if thread.isRunning():
                _ORPHANED_SEARCH_THREADS.append(thread)
        self._search_threads.clear()

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

        try:
            self._window.ui.preview_window.close_preview(False)
        except AttributeError:
            self._window.ui.preview_window.close()
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
        """Connect application signals, grouped by the surface they belong to."""

        self._connect_library_signals()
        self._connect_gallery_signals()
        self._connect_menu_signals()
        self._connect_navigation_signals()
        self._connect_status_bar_signals()

    def _connect_library_signals(self) -> None:
        """Library tree, scan pipeline and sidebar routing signals."""

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

    def _connect_gallery_signals(self) -> None:
        """Grid, viewer and map interactions."""

        ui = self._window.ui

        # Grid interactions — double-click opens the detail view. A single click only
        # selects: opening on click made an accidental click eject the user into the
        # detail page.
        ui.grid_view.itemDoubleClicked.connect(self._on_asset_clicked)
        ui.grid_view.visibleRowsChanged.connect(self._asset_list_vm.prioritize_rows)
        ui.grid_view.visibleRowsChanged.connect(self._on_gallery_scrolled)

        # Background AI scans step aside while the user is actually looking at
        # something: leaving them running made viewing and scrolling sluggish,
        # because face and OCR inference read full-size images and hold CPU.
        self._view_router.detailViewShown.connect(self._enter_foreground_viewing)
        self._view_router.galleryViewShown.connect(self._leave_foreground_viewing)
        self._view_router.mapViewShown.connect(self._leave_foreground_viewing)
        map_view = self._view_router.map_view()
        if map_view is not None:
            signal = getattr(map_view, "userInteracting", None)
            if signal is not None:
                signal.connect(self._touch_foreground_viewing)
        self._view_router.peopleViewShown.connect(self._leave_foreground_viewing)
        self._scroll_idle_timer = QTimer(self)
        self._scroll_idle_timer.setSingleShot(True)
        self._scroll_idle_timer.setInterval(400)
        self._scroll_idle_timer.timeout.connect(self._on_scroll_idle)
        self._scroll_viewing_registered = False

        # Filmstrip clicks are now handled by PlaybackCoordinator

        # Connect favorite click from grid view
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
        ui.detail_page.prev_button.clicked.connect(self._playback.select_previous)
        ui.detail_page.next_button.clicked.connect(self._playback.select_next)
        # Map view cluster interactions
        ui.map_view.assetActivated.connect(self._on_map_asset_activated)
        ui.map_view.clusterActivated.connect(self._on_cluster_activated)
        ui.map_view.showAllRequested.connect(self._handle_map_show_all_requested)
        # Map feature buttons (in map source bar, to the right of map selector)
        ui.map_view.btn_person_filter.clicked.connect(self._on_person_filter_toggled)
        ui.map_view.btn_trail_toggle.clicked.connect(self._on_trail_toggle_toggled)

    def _connect_menu_signals(self) -> None:
        """Menu actions, buttons and the AI status surfaces."""

        ui = self._window.ui

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
        ui.main_header.search_submitted_with_scope.connect(
            self._handle_search_requested
        )
        ui.main_header.search_popup_opened.connect(self._prepare_search_popup)
        ui.main_header.similar_search_requested.connect(self._handle_similar_search)
        ui.main_header.search_history_clear_requested.connect(
            self._clear_search_history
        )
        self._thumbnail_service.thumbnailReady.connect(
            self._on_popup_thumbnail_ready
        )
        ui.main_header.search_precision_group.triggered.connect(
            self._handle_search_precision_changed
        )
        ui.sidebar.searchEntrySelected.connect(self._handle_search_entry_selected)
        ui.main_header.toggle_semantic_search_action.toggled.connect(
            self._handle_semantic_search_toggle
        )
        # Agent Organize Features
        ui.main_header.find_duplicates_action.triggered.connect(self._handle_find_duplicates)
        ui.main_header.smart_album_event_action.triggered.connect(
            lambda: self._handle_create_smart_album("event")
        )
        ui.main_header.smart_album_location_action.triggered.connect(
            lambda: self._handle_create_smart_album("location")
        )
        ui.main_header.smart_album_time_action.triggered.connect(
            lambda: self._handle_create_smart_album("time")
        )
        ui.main_header.smart_album_theme_action.triggered.connect(
            lambda: self._handle_create_smart_album("theme")
        )
        # Info Button
        ui.info_button.clicked.connect(self._playback.toggle_info_panel)
        # Back Button (detail page)
        ui.back_button.clicked.connect(self._detail_vm.back_to_gallery)
        # Fullscreen Button — already connected in window_manager._configure_window_controls

        # Map Button (detail page)
        ui.map_button.clicked.connect(self._toggle_detail_map)
        # Gallery page back button for cluster gallery mode
        ui.gallery_page.backRequested.connect(self._gallery_vm.return_from_cluster_gallery)
        # Search back button
        ui.gallery_page.searchBackRequested.connect(self._on_search_back)
        # Dashboard Click
        ui.albums_dashboard_page.albumSelected.connect(self.open_album_from_path)
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
        self._context.library.ocrScanStatusChanged.connect(
            self._on_ocr_scan_status
        )
        # Sidebar checkbox filtering
        ui.sidebar.albumCheckStateChanged.connect(self._on_album_check_state_changed)
        self._restore_checked_library_paths()

    def _connect_navigation_signals(self) -> None:
        """Navigation entry points and stored preferences."""

        ui = self._window.ui

        # Navigation
        self._navigation.bindLibraryRequested.connect(self._dialog.bind_library_dialog)
        ui.change_workspace_action.triggered.connect(self._on_change_workspace)
        ui.run_setup_action.triggered.connect(self._on_run_setup_wizard)
        ui.bind_library_action.triggered.connect(self._dialog.bind_library_dialog)
        self._detail_vm.edit_requested.connect(self._edit.enter_edit_mode)

        # Preferences (Wheel, Volume) - Filmstrip handled in PlaybackCoordinator
        self._restore_preferences()
        ui.wheel_action_group.triggered.connect(self._handle_wheel_action_changed)

    def _connect_status_bar_signals(self) -> None:
        """Status bar panels, theme/language switches and search model sync."""

        ui = self._window.ui
        updates = self._facade.library_updates

        # Status Bar Connections (Restored)
        # Facade Signals -> Status Bar
        # Note: AppFacade exposes library_updates (ScannerSignals)
        updates.scanProgress.connect(self._status_bar.handle_scan_progress)
        updates.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._facade.scanBatchFailed.connect(self._status_bar.handle_scan_batch_failed)
        self._facade.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._facade.scanFinished.connect(self._status_bar.handle_scan_finished)
        # Semantic search needs a CLIP index; without it every search fails with
        # "no index found". Generation is expensive, so it runs once per session and
        # only when the repository reports missing embeddings.
        self._facade.scanFinished.connect(
            lambda *_: self._ensure_embedding_index()
        )

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

        # The search model can be changed from the wizard or the settings window;
        # both only write the setting, and this keeps the runtime in sync.
        from iPhoto.agent.services import search_models as _search_models

        self._context.settings.settingsChanged.connect(
            self._on_search_model_setting_changed
        )
        self._context.model_selection.apply(
            "search", self._context.settings.get(_search_models.SETTINGS_KEY, None)
        )
        current_lang = self._context.settings.get("ui.language", "zh")
        if current_lang == "en":
            ui.language_en.setChecked(True)
        else:
            ui.language_zh.setChecked(True)

        self._context.language.languageChanged.connect(lambda _lang: ui.main_header.retranslate())
        self._context.language.languageChanged.connect(lambda _lang: ui.sidebar.retranslate())
        self._context.language.languageChanged.connect(
            lambda _lang: ui.grid_view.retranslate()
        )

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
            if bound_people_service is not None:
                people_page.set_people_service(bound_people_service)
            else:
                people_page.set_library_root(root)
            people_page.set_status_message(self._context.library.face_scan_status_message())
        map_runtime = self._map_runtime()
        map_interaction_service = self._map_interaction_service()
        self._map_extension_download.set_package_root(
            self._resolve_map_package_root(map_runtime)
        )
        if ui is not None:
            ui.map_view.set_map_runtime(map_runtime)
            ui.map_view.set_map_interaction_service(map_interaction_service)
        if ui is not None:
            ui.info_panel.set_map_runtime(map_runtime)
        playback = getattr(self, "_playback", None)
        if playback is not None:
            playback.set_map_runtime(map_runtime)
            if bound_people_service is not None:
                playback.set_people_service(bound_people_service)
            else:
                playback.set_people_library_root(root)

    def _active_session(self):
        return getattr(self._context, "library_session", None)

    def _active_search_spec(self):
        """Search model currently selected, as recorded by bootstrap."""

        return self._context.model_selection.spec("search")

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
        self._window.ui.map_view.set_focused_asset_mode(False)
    def _add_header_back_button(self) -> None:
        """Add a back button to the main header bar."""
        ui = self._window.ui
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

    def _try_focus_map(self, lat: float, lon: float, attempt: int) -> None:
        """Center the Location map on the given coordinates at high zoom."""
        ui = self._window.ui
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
            logging.getLogger(__name__).debug(
                "Could not focus the map yet (attempt %s)", attempt, exc_info=True
            )
            self._retry_focus(lat, lon, attempt)

    def _retry_focus(self, lat: float, lon: float, attempt: int) -> None:
        if attempt < MAP_FOCUS_MAX_RETRIES:
            QTimer.singleShot(
                MAP_FOCUS_RETRY_INTERVAL_MS,
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

    def _paths_equal(self,first,second) -> bool:
        """Delegate to the single shared comparison."""

        return paths_equal(first,second)

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
        """视频播放状态改变时，让后台扫描让路或恢复。"""
        library_manager = getattr(self._context, "library", None)
        if library_manager is None:
            return
        if is_playing:
            library_manager.register_foreground_viewing()
        else:
            library_manager.unregister_foreground_viewing()

    def _foreground_library(self):
        return getattr(self._context, "library", None)

    def _enter_foreground_viewing(self) -> None:
        """用户正在看内容：暂停人脸/OCR 扫描，把 CPU 与磁盘让给界面。"""
        library_manager = self._foreground_library()
        if library_manager is not None:
            library_manager.register_foreground_viewing()

    def _leave_foreground_viewing(self) -> None:
        """离开浏览状态：恢复后台扫描。"""
        library_manager = self._foreground_library()
        if library_manager is not None:
            library_manager.unregister_foreground_viewing()

    def _touch_foreground_viewing(self) -> None:
        """Register brief foreground viewing: background scans yield for a moment.

        Used by every surface the user actively manipulates (grid scrolling, map
        panning/zooming): the scans resume shortly after the interaction stops, so
        they still make progress in the background without competing with the UI.
        """

        if not self._scroll_viewing_registered:
            self._scroll_viewing_registered = True
            library_manager = self._foreground_library()
            if library_manager is not None:
                library_manager.register_foreground_viewing()
        self._scroll_idle_timer.start()

    def _on_gallery_scrolled(self, first: int, last: int) -> None:
        """滚动期间短暂让路，滚动停下后自动恢复。

        滚动本身很轻，但它会触发换窗加载；如果这时人脸/OCR 正在推理，新窗口的
        读取就会被拖慢。登记一小段时间既能保住手感，也不会让扫描长期停摆。
        """
        del first, last
        self._touch_foreground_viewing()

    def _on_scroll_idle(self) -> None:
        """滚动停止：释放滚动期间的登记。"""
        if not self._scroll_viewing_registered:
            return
        self._scroll_viewing_registered = False
        library_manager = self._foreground_library()
        if library_manager is not None:
            library_manager.unregister_foreground_viewing()

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

    def _record_search_history(self, query: str) -> None:
        """Remember the query so the search dropdown can offer it again."""

        text = (query or "").strip()
        if not text:
            return
        try:
            history = list(self._context.settings.get("agent.search_history", []) or [])
        except Exception:
            history = []
        history = [text] + [item for item in history if item != text]
        self._context.settings.set("agent.search_history", history[:10])

    def _clear_search_history(self) -> None:
        self._context.settings.set("agent.search_history", [])
        self._window.ui.main_header.set_search_history([])

    def _current_album_rel_path(self) -> str | None:
        """Library-relative path of the album being viewed, if any."""

        root = self._context.library.root()
        active = getattr(self._gallery_vm, "active_root", None)
        active_path = getattr(active, "value", active)
        if root is None or active_path is None:
            return None
        try:
            relative = (
                Path(active_path).resolve().relative_to(Path(root).resolve()).as_posix()
            )
        except (OSError, ValueError):
            return None
        return None if relative in ("", ".") else relative

    def _current_album_label(self) -> str | None:
        relative = self._current_album_rel_path()
        return Path(relative).name if relative else None

    def _prepare_search_popup(self) -> None:
        """Fill the dropdown with history, scope and visual shortcuts."""

        header = self._window.ui.main_header
        try:
            history = list(self._context.settings.get("agent.search_history", []) or [])
        except Exception:
            history = []
        header.set_search_history(history[:10])
        header.set_album_scope(self._current_album_label())
        self._populate_popup_similar()

    def _populate_popup_similar(self) -> None:
        """Offer a few thumbnails from the current view for "find similar"."""

        header = self._window.ui.main_header
        size = QSize(72, 72)
        self._popup_similar_paths: dict[Path, str] = {}
        items: list[tuple[str, object]] = []

        store = self._gallery_store
        try:
            count = min(8, store.count())
        except Exception:
            count = 0

        for row in range(count):
            try:
                asset = store.asset_at(row)
            except Exception:
                asset = None
            if asset is None or asset.abs_path is None:
                continue
            asset_id = str(getattr(asset, "id", "") or "")
            if not asset_id:
                continue
            path = Path(asset.abs_path)
            self._popup_similar_paths[path] = asset_id
            pixmap = self._thumbnail_service.get_thumbnail(path, size)
            if pixmap is not None:
                items.append((asset_id, pixmap))

        header.set_similar_photos(items)

    def _on_popup_thumbnail_ready(self, path: Path) -> None:
        """Fill a suggested thumbnail as soon as the cache produces it."""

        target = Path(path)
        asset_id = getattr(self, "_popup_similar_paths", {}).get(target)
        if not asset_id:
            return
        header = self._window.ui.main_header
        popup = getattr(header, "_search_popup", None)
        if popup is None or not popup.isVisible():
            return
        pixmap = self._thumbnail_service.get_thumbnail(target, QSize(72, 72))
        if pixmap is not None:
            header.add_similar_photo(asset_id, pixmap)

    def _handle_similar_search(self, asset_id: str) -> None:
        """Search for photos that look like *asset_id*."""

        self._handle_search_requested(
            tr("search.similar_query"), "all", similar_to=asset_id
        )

    def _on_ocr_scan_status(self, status: object) -> None:
        """Surface the OCR backfill in the status bar.

        Skipped while the user is looking at search results so the result count
        is not replaced by background-scan chatter. The scan layer sends a
        :class:`ScanStatus` key instead of a finished sentence, so "this is the
        last message" comes from the payload rather than from searching the text
        for a word like ``完成`` (which broke as soon as the UI was translated).
        """

        if not isinstance(status, ScanStatus) or not status.key:
            return
        visible = getattr(self, "_is_search_page_visible", None)
        if callable(visible) and visible():
            return
        message = tr(status.key, **dict(status.params))
        if status.completed:
            self._status_bar.show_message(message, 5000)
        else:
            self._status_bar.show_message(message)

    def _handle_search_requested(
        self, query: str, scope: str = "all", *, similar_to: str | None = None
    ) -> None:
        """Handle semantic search request from the search input."""
        self._logger.info("Search requested: %s (scope=%s)", query, scope)

        state = self._search_state
        if (
            state is not None
            and not state.get("done")
            and state.get("query") == query
            and state.get("similar_to") == similar_to
        ):
            # Same query is already searching (e.g. Enter pressed twice): show the
            # panel again instead of spawning a second worker that loads the model
            # in parallel.
            self._view_router.show_gallery()
            self._show_search_loading(query)
            return

        if similar_to is None:
            self._record_search_history(query)

        self._search_serial += 1
        serial = self._search_serial
        self._search_state = {
            "query": query,
            "results": [],
            "done": False,
            "error": "",
            "serial": serial,
            "scope": scope,
            "similar_to": similar_to,
        }
        # A dedicated sidebar entry keeps the search reachable while it loads, so
        # the user can browse other views instead of waiting on the loading panel.
        self._window.ui.sidebar.set_search_state(query, loading=True)

        # Switch to gallery view
        self._view_router.show_gallery()

        # Show loading
        self._show_search_loading(query)

        # Run search in background thread
        from PySide6.QtCore import QThread, Signal

        # Get paths before starting thread
        from iPhoto.agent.services import search_models

        library_root = Path(self._context.library.root())
        model_dir = search_models.model_dir(self._active_search_spec())
        # Reuse the session's embedding service: it is warmed before the face scan
        # starts, so rebuilding a service here would reload the weights on every
        # single search.
        library_session = self._active_session()
        # The nested worker's ``self`` is the thread, so the coordinator is captured
        # once here: ``self._active_search_spec()`` inside ``run`` raised
        # AttributeError, which made every search with a bound session fail.
        coordinator = self

        def on_finished(results, error):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(
                0, lambda: self._finish_search(serial, results, error, query)
            )

        def on_progress(message, current, total):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(
                0,
                lambda: self._update_search_progress(
                    serial, message, current, total
                ),
            )

        thread = SearchThread(
            query,
            model_dir,
            library_root,
            self._active_search_spec(),
            library_session=library_session,
            precision=self._search_precision(),
            scope_album_path=(
                self._current_album_rel_path() if scope == "album" else None
            ),
            similar_to=similar_to,
        )
        thread.finished.connect(on_finished)
        thread.progress.connect(on_progress)
        thread.finished.connect(lambda *_: self._retire_search_thread(thread))
        self._search_thread = thread
        self._search_threads.append(thread)
        thread.start()

    def _retire_search_thread(self, thread) -> None:
        """Release a finished search worker without destroying a running one."""

        thread.wait(1000)
        try:
            self._search_threads.remove(thread)
        except ValueError:
            pass
        if getattr(self, "_search_thread", None) is thread:
            self._search_thread = None
        thread.deleteLater()

    def _display_search_results_raw(self, results: list, query: str) -> None:
        """Display search results."""
        ui = self._window.ui
        # Search results live in the grid; make sure it is the view the page
        # returns to, even when the search was started from the Home view.
        ui.gallery_page.show_grid()
        ui.gallery_page.hide_loading()
        if not results:
            # Order matters: paint the "no matches" state first so a failure in
            # the model update cannot leave the previous search's photos visible.
            ui.gallery_page.show_search_results_header(query, 0)
            ui.gallery_page.show_search_empty(query)
            try:
                self._gallery_vm.show_search_results([])
            except Exception:
                self._logger.debug("could not clear search results", exc_info=True)
            self._status_bar.show_message(tr("search.empty.title", query=query))
            return

        # Show search results header with back button
        total_count = len(results)
        ui.gallery_page.show_search_results_header(query, total_count)
        asset_ids = [r["asset_id"] for r in results]
        self._gallery_vm.show_search_results(asset_ids)

        # Scroll to top
        ui.grid_view.scrollToTop()
        self._status_bar.show_message(f"找到 {total_count} 张与 '{query}' 相关的照片")

    def _finish_search(self, serial: int, results, error: str, query: str) -> None:
        """Complete a search and decide whether to show or announce the results."""

        state = self._search_state
        if (
            state is None
            or state.get("serial") != serial
            or state.get("query") != query
        ):
            return  # superseded by a newer search

        state["done"] = True
        state["error"] = error or ""
        state["results"] = list(results or [])

        sidebar = self._window.ui.sidebar
        if error:
            sidebar.set_search_state(query, failed=True)
        else:
            sidebar.set_search_state(query, count=len(state["results"]))

        if self._is_search_page_visible():
            if error:
                self._display_search_error(error)
            else:
                self._display_search_results_raw(state["results"], query)
            return

        # The user navigated away while the search was running: keep the result
        # available in the sidebar and announce it instead of switching views.
        toast = getattr(self._window.ui, "notification_toast", None)
        if error:
            self._status_bar.show_message(f"搜索失败：{error}", 8000)
        elif state["results"]:
            self._status_bar.show_message(
                f"搜索完成：找到 {len(state['results'])} 张，"
                f"点击左侧「搜索：{query}」查看",
                10000,
            )
            if toast is not None:
                toast.show_toast(f"搜索完成 · {len(state['results'])} 张")
        else:
            self._status_bar.show_message(f"没有找到与 '{query}' 相关的照片", 8000)
            if toast is not None:
                toast.show_toast("搜索完成 · 0 张")

    def _handle_search_entry_selected(self) -> None:
        """Open the active search (loading panel or its results) from the sidebar."""

        state = self._search_state
        if state is None:
            return
        # The search panel and its results belong to the grid view; leaving Home
        # as the page's base view would hide them once loading finishes.
        page = getattr(self._window.ui, "gallery_page", None)
        if page is not None and hasattr(page, "show_grid"):
            page.show_grid()
        self._view_router.show_gallery()
        if not state.get("done"):
            self._show_search_loading(state["query"])
            return
        error = state.get("error") or ""
        if error:
            self._display_search_error(error)
            return
        self._display_search_results_raw(state.get("results") or [], state["query"])

    def _is_search_page_visible(self) -> bool:
        """Return whether the gallery is currently showing the search panel."""

        page = getattr(self._window.ui, "gallery_page", None)
        stack = getattr(page, "_stack", None)
        loading = getattr(page, "_loading_widget", None)
        return (
            stack is not None
            and loading is not None
            and stack.currentWidget() is loading
        )

    def _show_search_loading(self, query: str, sub_message: str = None, show_continue: bool = False) -> None:
        """Show loading state in the grid view area."""
        ui = self._window.ui
        ui.gallery_page.show_search_loading(query)
    def _update_search_progress(self, serial: int, message: str, current: int, total: int) -> None:
        """Update search progress in the UI."""

        state = self._search_state
        if state is None or state.get("serial") != serial:
            return
        if not self._is_search_page_visible():
            # The user navigated away; do not pull them back into the search panel.
            return
        ui = self._window.ui
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
        ui.gallery_page.show_error("搜索失败", error)
        self._status_bar.show_message(f"搜索失败: {error}", 10000)

    def _on_search_back(self) -> None:
        """Handle back button click during search."""
        # Hide search results header
        ui = self._window.ui
        self._search_state = None
        ui.sidebar.clear_search()
        ui.gallery_page.hide_search_results_header()
        # Return to normal gallery view
        self._gallery_vm.open_all_photos()
        self._status_bar.show_message("")

    def _search_precision(self) -> str:
        """Return the configured search precision tier."""

        value = self._context.settings.get("agent.search_precision", "balanced")
        return value if value in {"strict", "balanced", "loose"} else "balanced"

    def _handle_search_precision_changed(self, action) -> None:
        """Persist the precision tier picked from the settings menu."""

        tier = action.data()
        if tier in {"strict", "balanced", "loose"}:
            self._context.settings.set("agent.search_precision", tier)

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

    def _on_search_model_setting_changed(self, key: str, value: object) -> None:
        """React to a model change made by the wizard or the settings window."""

        from iPhoto.agent.services import ai_features, search_models

        if key == search_models.REBUILD_KEY:
            self._rebuild_search_index()
            return
        if key in (ai_features.FACE_REBUILD_KEY, ai_features.OCR_REBUILD_KEY):
            self._rebuild_ai_index("face" if key == ai_features.FACE_REBUILD_KEY else "ocr")
            return
        if key != search_models.SETTINGS_KEY:
            return
        spec = self._context.model_selection.apply(
            "search", str(value) if value else None
        )
        self._logger.info("search model switched to %s", spec.key)

        self._context.search_index_status.set_model(spec.key)
        session = getattr(self._context, "library_session", None)
        reset = getattr(session, "reset_embedding_service", None)
        if callable(reset):
            reset()
        clear_search_embedding_cache()
        if not search_models.is_installed(spec):
            self._status_bar.show_message(
                tr("search.model.missing", mb=spec.download_mb), 8000
            )
            self._download_search_model_then_index(spec)
            return
        # Vectors from the previous model stay on disk (so switching back is
        # instant) but search ignores them; fill in the new model's rows.
        self._start_embedding_generation_silent()

    def _rebuild_ai_index(self, feature: str) -> None:
        """Re-run the face or text scan for photos that still need it.

        Both workers pull the remaining ``pending`` rows as their queue drains,
        so a library rescan is enough to backfill them; nothing is thrown away.
        """

        root = self._session.library_root if self._session is not None else None
        if root is None:
            self._status_bar.show_message(tr("settings.ai_state.no_library"), 6000)
            return
        title = (
            tr("status.task.face") if feature == "face" else tr("status.task.ocr")
        )
        tasks = self._context.background_tasks

        tasks.start(feature, title)
        self._status_bar.show_message(tr("settings.ai_state.scanning", title=title), 8000)
        try:
            self._gallery_vm.rescan_current()
        except Exception:  # pragma: no cover - surfaced in the status bar
            self._logger.warning("could not start the %s rebuild", feature, exc_info=True)
            tasks.finish(feature, failed=True)

    def _rebuild_search_index(self) -> None:
        """Re-embed the whole library with the active model."""

        from iPhoto.agent.services import search_models

        session = getattr(self._context, "library_session", None)
        reset = getattr(session, "reset_embedding_service", None)
        if callable(reset):
            reset()
        clear_search_embedding_cache()
        spec = self._active_search_spec()
        if not search_models.is_installed(spec):
            self._status_bar.show_message(
                tr("search.model.missing", mb=spec.download_mb), 8000
            )
            return
        self._status_bar.show_message(tr("search.model.rebuild_started"), 5000)
        self._start_embedding_generation_silent(force_rebuild=True)

    def _download_search_model_then_index(self, spec) -> None:
        """Fetch missing weights in the background, then index with them."""

        from iPhoto.gui.services import embedding_bootstrap

        worker = SearchModelDownloadWorker(
            spec,
            tasks=self._context.background_tasks,
            status_callback=self._show_status_message,
            on_downloaded=self._start_embedding_generation_silent,
        )
        embedding_bootstrap.QThreadPool.globalInstance().start(worker)

    def _show_status_message(self, message: str, timeout_ms: int = 5000) -> None:
        """Show *message* in the status bar, marshalled onto the GUI thread."""

        from PySide6.QtCore import QTimer

        QTimer.singleShot(
            0, lambda: self._status_bar.show_message(message, timeout_ms)
        )

    def _ensure_embedding_index(self) -> None:
        """Start semantic-index generation once, if it is missing."""

        if getattr(self, "_embedding_bootstrap_started", False):
            return
        self._embedding_bootstrap_started = True
        try:
            self._start_embedding_generation_silent()
        except Exception:
            self._logger.debug("Embedding bootstrap failed", exc_info=True)

    def _start_embedding_generation_silent(self, *, force_rebuild: bool = False) -> None:
        """Start embedding generation silently in background with status bar updates."""

        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        from iPhoto.gui.services import embedding_bootstrap

        worker = EmbeddingIndexWorker(
            library_session,
            model_spec=self._active_search_spec(),
            status=self._context.search_index_status,
            tasks=self._context.background_tasks,
            status_callback=self._show_status_message,
            force_rebuild=force_rebuild,
        )
        embedding_bootstrap.QThreadPool.globalInstance().start(worker)

    def _start_embedding_generation(self) -> None:
        """Start background embedding generation with progress UI (when user searches)."""

        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        # Show progress in the gallery page with progress bar
        ui = self._window.ui
        ui.gallery_page.show_loading_message(
            "正在初始化 AI 搜索...",
            "首次使用需要为照片生成索引，之后搜索会很快",
            show_progress=True,
        )

        def on_status(status_type, current, total, message):
            from PySide6.QtCore import QTimer

            QTimer.singleShot(
                0,
                lambda: self._handle_embedding_status(
                    status_type, current, total, message
                ),
            )

        from iPhoto.gui.services import embedding_bootstrap

        worker = EmbeddingInitWorker(
            library_session,
            model_spec=self._active_search_spec(),
            callback=on_status,
        )
        embedding_bootstrap.QThreadPool.globalInstance().start(worker)

    def _handle_embedding_status(self, status_type: str, current: int, total: int, message: str) -> None:
        """Handle embedding generation status updates."""
        ui = self._window.ui
        self._logger.info(f"Embedding status: {status_type} - {message}")

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
        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        # Duplicate detection and smart albums read album metadata only; they no
        # longer need the CLIP embedding service, and gating them on it made both
        # features unavailable (and could trigger a model-download dialog).
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
            asset_repository=library_session.asset_runtime.assets,
            library_root=library_session.library_root,
        )

        def on_complete(duplicates):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._display_duplicates(duplicates))

        worker = DuplicateWorker(organize_service, on_complete)
        QThreadPool.globalInstance().start(worker)

        self._status_bar.show_message(
            tr("organize.searching_duplicates")
        )

    def _display_duplicates(self, duplicates: list) -> None:
        """Display duplicate photos in the gallery."""
        if not duplicates:
            self._status_bar.show_message(
                tr("organize.no_duplicates")
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
            tr(
                "organize.duplicates_found",
                groups=total_groups,
                photos=total_photos,
            )
        )

    def _handle_create_smart_album(self, group_by: str) -> None:
        """Handle create smart album action.

        Parameters
        ----------
        group_by : str
            How to group photos: 'event', 'location', 'time', 'theme'.
        """
        library_session = getattr(self._context, "library_session", None)
        if library_session is None:
            return

        # Duplicate detection and smart albums read album metadata only; they no
        # longer need the CLIP embedding service, and gating them on it made both
        # features unavailable (and could trigger a model-download dialog).
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
            asset_repository=library_session.asset_runtime.assets,
            library_root=library_session.library_root,
        )

        def on_complete(albums):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._display_smart_albums(albums, group_by))

        worker = SmartAlbumWorker(organize_service, group_by, on_complete)
        QThreadPool.globalInstance().start(worker)

        self._status_bar.show_message(
            tr(
                "organize.creating_albums",
                group_by=tr(f"organize.group.{group_by}"),
            )
        )

    def _display_smart_albums(self, albums: list, group_by: str) -> None:
        """Display smart album suggestions."""
        if not albums:
            self._status_bar.show_message(
                tr("organize.no_albums")
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

    def _on_run_setup_wizard(self) -> None:
        """Re-open the first-run setup wizard from the settings menu."""

        self._context.show_welcome_wizard()

    def _on_change_workspace(self) -> None:
        """Copy the workspace to a new folder, then switch the setting."""

        from ..ui.controllers.workspace_relocation_controller import (
            relocate_workspace_interactively,
        )

        root = self._context.library.root()
        changed = relocate_workspace_interactively(
            self._window, root, self._context.settings
        )
        if changed:
            self._status_bar.show_message(
                "存储位置已更改，请重启应用以生效。", 8000
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
            show_face_names = is_truthy(stored_face_names)
        else:
            show_face_names = bool(stored_face_names)
        ui.toggle_face_names_action.setChecked(show_face_names)
        self._playback.set_face_name_display_enabled(show_face_names)

        stored_hidden_people = settings.get("ui.show_hidden_people", False)
        if isinstance(stored_hidden_people, str):
            show_hidden_people = is_truthy(stored_hidden_people)
        else:
            show_hidden_people = bool(stored_hidden_people)
        ui.toggle_hidden_people_action.setChecked(show_hidden_people)
        ui.people_page.set_show_hidden_people(show_hidden_people)
        # 3. Semantic Search
        stored_semantic_search = settings.get("agent.semantic_search_enabled", False)
        if isinstance(stored_semantic_search, str):
            semantic_search_enabled = is_truthy(stored_semantic_search)
        else:
            semantic_search_enabled = bool(stored_semantic_search)
        ui.main_header.toggle_semantic_search_action.setChecked(semantic_search_enabled)
        precision = self._search_precision()
        precision_action = getattr(ui.main_header, "search_precision_actions", {}).get(
            precision
        )
        if precision_action is not None:
            precision_action.setChecked(True)
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
            initial_muted = is_truthy(stored_muted)
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


#: Cache of ``[(asset_id, lowercase place name)]`` per library root; built once
#: because reverse geocoding every geotagged asset on each keystroke is wasteful.

