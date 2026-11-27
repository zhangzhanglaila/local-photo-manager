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
    QModelIndex,
    QObject,
    Qt,
    QThreadPool,
    QTimer,
)

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.gui.coordinators.ai_index_coordinator import AiIndexCoordinator
from iPhoto.gui.coordinators.gallery_interaction_coordinator import (
    GalleryInteractionCoordinator,
)
from iPhoto.gui.coordinators.location_coordinator import (
    MAP_FOCUS_MAX_RETRIES,
    MAP_FOCUS_RETRY_INTERVAL_MS,
    LocationCoordinator,
)
from iPhoto.gui.coordinators.library_events_coordinator import LibraryEventsCoordinator
from iPhoto.gui.coordinators.library_open_coordinator import LibraryOpenCoordinator
from iPhoto.gui.coordinators.library_session_access import LibrarySessionAccess
from iPhoto.gui.coordinators.library_tree_binder import LibraryTreeBinder
from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.coordinators.organize_coordinator import OrganizeCoordinator
from iPhoto.gui.coordinators.people_navigation_coordinator import (
    PeopleNavigationCoordinator,
)
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.coordinators.search_coordinator import SearchCoordinator
from iPhoto.gui.coordinators.startup_tasks import StartupTasks
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.coordinators.window_chrome_coordinator import WindowChromeCoordinator
from iPhoto.gui.coordinators.window_preferences import WindowPreferences
from iPhoto.gui.coordinators.window_signal_wiring import WindowSignalWiring
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
from iPhoto.gui.services.ai_index_status import AiIndexStatusPresenter
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
        self._startup_loading = True
        self._startup_tasks = StartupTasks(window, context, parent=self)
        self._map_extension_download = MapExtensionDownloadController(
            window,
            context,
            package_root=self._resolve_map_package_root(self._session_access.map_runtime()),
        )
        window.ui.download_map_extension_action.setEnabled(supports_map_extension_download())
        self._event_bus = context.event_bus
        edit_service_getter = self._session_access.edit_service
        asset_state_service = self._session_access.asset_state_service()

        # --- ViewModels Setup ---
        lib_root = self._session_access.library_root()
        self._context.asset_runtime.bind_library_root(lib_root)
        self._asset_list_vm = GalleryListModelAdapter.create(
            asset_query_service=self._session_access.asset_query_service(),
            thumbnail_service=self._context.asset_runtime.thumbnail_service,
            edit_service_getter=edit_service_getter,
            on_model_populated=window.ui.grid_view.dismiss_loading_state,
            library_root=lib_root,
            parent=window.ui.grid_view,
        )
        self._gallery_store = self._asset_list_vm.store
        self._media_session = MediaSelectionSession()
        self._media_session.bind_collection(self._gallery_store)
        self._thumbnail_service = self._context.asset_runtime.thumbnail_service
        bound_people_service = self._session_access.people_service(library_root=lib_root)
        self._playback_people_service = bound_people_service or PeopleService()
        if bound_people_service is not None:
            window.ui.people_page.set_people_service(self._playback_people_service)
        else:
            window.ui.people_page.set_library_root(lib_root)
        window.ui.people_page.set_status_message(context.library.face_scan_status_message())
        self._pinned_items_service = PinnedItemsService(
            context.settings,
            people_service_getter=self._session_access.people_service,
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

        self._people_navigation = PeopleNavigationCoordinator(
            window,
            self._gallery_vm,
            self._view_router,
            parent=self,
        )

        self._library_events = LibraryEventsCoordinator(
            window,
            context,
            pinned_items_service=self._pinned_items_service,
            thumbnail_service=self._thumbnail_service,
            gallery_vm=self._gallery_vm,
            parent=self,
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
            map_runtime=self._session_access.map_runtime(),
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        self._navigation.set_playback_coordinator(self._playback)
        context.library.peopleSnapshotCommitted.connect(
            self._library_events.on_people_snapshot
        )
        # Manually attach info panel if available
        window.ui.info_panel.set_map_runtime(self._session_access.map_runtime())
        self._playback.set_info_panel(window.ui.info_panel)
        window.ui.info_panel.downloadMapExtensionRequested.connect(
            lambda: self._map_extension_download.start_download(source="info_panel")
        )
        window.ui.map_view.set_map_runtime(self._session_access.map_runtime())
        window.ui.map_view.set_map_interaction_service(
            self._session_access.map_interaction_service()
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

        self._search = SearchCoordinator(
            window,
            context,
            gallery_vm=self._gallery_vm,
            gallery_store=self._gallery_store,
            view_router=self._view_router,
            status_bar=self._status_bar,
            thumbnail_service=self._thumbnail_service,
            parent=self,
        )

        self._organize = OrganizeCoordinator(
            context,
            gallery_vm=self._gallery_vm,
            status_bar=self._status_bar,
            parent=self,
        )

        self._window_preferences = WindowPreferences(
            window,
            context,
            playback=self._playback,
            search=self._search,
            gallery_vm=self._gallery_vm,
            start_embedding_generation=self._start_embedding_generation,
            parent=self,
        )

        self._library_open = LibraryOpenCoordinator(
            window,
            context,
            dialog=self._dialog,
            navigation=self._navigation,
            status_bar=self._status_bar,
            facade=self._facade,
            library_root_getter=self._session_access.library_root,
            on_library_changed=self._on_library_tree_updated,
            parent=self,
        )

        self._chrome = WindowChromeCoordinator(
            window,
            context,
            facade=self._facade,
            status_bar=self._status_bar,
            dialog=self._dialog,
            on_move_finished=self._library_events.on_move_finished,
            parent=self,
        )

        self._location = LocationCoordinator(
            window=window,
            gallery_vm=self._gallery_vm,
            gallery_store=self._gallery_store,
            navigation=self._navigation,
            detail_vm=self._detail_vm,
            library_getter=lambda: getattr(self._context, "library", None),
            status_bar=self._status_bar,
            people_service_getter=lambda: getattr(
                self, "_playback_people_service", None
            ),
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

        self._gallery_interactions = GalleryInteractionCoordinator(
            window,
            context,
            gallery_vm=self._gallery_vm,
            gallery_store=self._gallery_store,
            asset_list_vm=self._asset_list_vm,
            dialog=self._dialog,
            facade=self._facade,
            selection_controller=self._selection_controller,
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

        self._ai_index = AiIndexCoordinator(
            context=self._context,
            window=self._window,
            gallery_vm=self._gallery_vm,
            status_bar=self._status_bar,
            session_getter=self._session_access.active_session,
            library_root_getter=self._ai_index_library_root,
        )

        self._connect_signals()

    def start(self):
        """Start the coordinator."""
        self._logger.debug("MainCoordinator started")
        self._startup_tasks.run()
        self._view_router.show_gallery()
        self._map_extension_download.maybe_prompt_on_startup()

        # No auto-start - only load when user actually searches

    def finish_startup(self):
        """Re-enable full tree-update cascade after startup sequence completes."""
        self._startup_loading = False
        self._logger.debug("Startup loading complete, tree-update cascade enabled")

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
        self._search.shutdown()

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
        """Connect all window signals through the wiring module."""

        WindowSignalWiring(self).connect()

    @property
    def _library_binding(self) -> LibraryTreeBinder:
        binder = self.__dict__.get("_library_binding_obj")
        if binder is None:
            binder = LibraryTreeBinder(
                self._window,
                self._context,
                asset_list_vm=self._asset_list_vm,
                gallery_vm=self._gallery_vm,
                detail_vm=self._detail_vm,
                playback=self._playback,
                map_extension_download=self._map_extension_download,
                library_root_getter=self._session_access.library_root,
                asset_query_getter=self._session_access.asset_query_service,
                asset_state_getter=self._session_access.asset_state_service,
                people_service_getter=self._session_access.people_service,
                map_runtime_getter=self._session_access.map_runtime,
                map_interaction_getter=self._session_access.map_interaction_service,
                is_startup_loading=lambda: self._startup_loading,
                set_playback_people_service=self._set_playback_people_service,
            )
            object.__setattr__(self, "_library_binding_obj", binder)
        return binder

    @property
    def _session_access(self) -> LibrarySessionAccess:
        access = self.__dict__.get("_session_access_obj")
        if access is None:
            access = LibrarySessionAccess(self._context)
            object.__setattr__(self, "_session_access_obj", access)
        return access

    def _set_playback_people_service(self, service) -> None:
        self._playback_people_service = service

    def _on_library_tree_updated(self) -> None:
        self._logger.debug(
            "_on_library_tree_updated: root=%s",
            self._session_access.library_root(),
        )
        self._library_binding.bind()

    # -- Location view --------------------------------------------------------
    # Focusing a photo on the map and getting back to it lives in
    # ``LocationCoordinator``; these wrappers keep the signal connections intact.

    def _toggle_detail_map(self) -> None:
        self._location.toggle_detail_map()

    def _handle_map_show_all_requested(self) -> None:
        self._location.show_all()

    def _handle_map_back_clicked(self) -> None:
        self._location.back_clicked()

    def _handle_sidebar_return_from_map(self, name: str) -> None:
        self._location.sidebar_return(name)

    def _handle_return_from_map(self) -> None:
        self._location.return_from_map()

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

    # -- AI index -------------------------------------------------------------
    # The lifecycle itself lives in ``AiIndexCoordinator``; these wrappers keep the
    # call sites (menu actions, settings changes, search flow) unchanged.

    def _handle_semantic_search_toggle(self, enabled: bool) -> None:
        self._ai_index.on_semantic_search_toggled(enabled)

    def _on_search_model_setting_changed(self, key: str, value: object) -> None:
        self._ai_index.on_search_model_setting_changed(key, value)

    def _rebuild_ai_index(self, feature: str) -> None:
        self._ai_index.rebuild_ai_feature(feature)

    def _rebuild_search_index(self) -> None:
        self._ai_index.rebuild_search_index()

    def _download_search_model_then_index(self, spec) -> None:
        self._ai_index.download_model_then_index(spec)

    def _show_status_message(self, message: str, timeout_ms: int = 5000) -> None:
        self._ai_index.show_status(message, timeout_ms)

    def _ensure_embedding_index(self) -> None:
        self._ai_index.ensure_index()

    def _start_embedding_generation_silent(self, *, force_rebuild: bool = False) -> None:
        self._ai_index.start_generation_silent(force_rebuild=force_rebuild)

    def _start_embedding_generation(self) -> None:
        self._ai_index.start_generation()

    def _ai_index_presenter(self) -> AiIndexStatusPresenter:
        return self._ai_index.presenter

    def _ai_index_library_root(self):
        """Library root the AI rebuild commands operate on."""

        session = self._session if self._session is not None else self._session_access.active_session()
        return getattr(session, "library_root", None)

    def _on_cluster_activated(self, assets: list):
        """Handle cluster click from map view to open cluster gallery.

        This is triggered when the user clicks a cluster with multiple assets
        on the map. Opens a gallery showing all assets in the cluster.
        """
        self._navigation.open_cluster_gallery(assets)

    def _on_map_asset_activated(self, rel: str) -> None:
        """Handle single-asset map activation inside the Location context."""

        self._navigation.open_location_asset(rel)

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

    # --- Public Accessors for Window ---
    def open_album_from_path(self, path: Path):
        """Open an album from the window/CLI; the flow lives in its own coordinator."""

        self._library_open.open_album_from_path(path)

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
