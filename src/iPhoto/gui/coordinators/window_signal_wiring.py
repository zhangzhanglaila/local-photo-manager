"""Signal assembly for the main window, separated from the coordinator state.

``MainCoordinator`` keeps the objects; this module only connects their signals.
Splitting the wiring out keeps the coordinator from turning every new widget into
another 30 lines of ``connect`` calls while leaving ownership untouched.
"""

from __future__ import annotations


class WindowSignalWiring:
    """Connect a ``MainCoordinator`` host's signals to its coordinators/views."""

    def __init__(self, host) -> None:
        self._host = host

    def connect(self) -> None:
        self._connect_library_signals()
        self._connect_gallery_signals()
        self._connect_menu_signals()
        self._connect_navigation_signals()
        self._connect_status_bar_signals()

    def _connect_library_signals(self) -> None:
        """Library tree, scan pipeline and sidebar routing signals."""

        ui = self._host._window.ui
        updates = self._host._facade.library_updates
        self._host._context.library.treeUpdated.connect(self._host._on_library_tree_updated)
        self._host._context.library.albumRenamed.connect(
            self._host._library_events.on_album_renamed
        )
        # Library watcher rescans still emit through the bound LibraryRuntimeController,
        # while facade-initiated rescans emit through LibraryUpdateService.
        self._host._context.library.scanChunkReady.connect(self._host._gallery_store.handle_scan_chunk)
        self._host._context.library.scanFinished.connect(self._host._gallery_store.handle_scan_finished)
        self._host._context.library.scanChunkReady.connect(self._host._gallery_vm.handle_location_scan_chunk)
        self._host._context.library.scanFinished.connect(self._host._gallery_vm.handle_location_scan_finished)
        updates.scanChunkReady.connect(self._host._gallery_store.handle_scan_chunk)
        updates.scanFinished.connect(self._host._gallery_store.handle_scan_finished)
        updates.scanChunkReady.connect(self._host._gallery_vm.handle_location_scan_chunk)
        updates.scanFinished.connect(self._host._gallery_vm.handle_location_scan_finished)
        self._host._gallery_vm.message_requested.connect(self._host._status_bar.show_message)

        # Return to detail photo after exploring Location view.
        # Click "All Photos" or "Location" again in sidebar to go back.
        ui.sidebar.allPhotosSelected.connect(self._host._handle_return_from_map)
        ui.sidebar.staticNodeSelected.connect(self._host._handle_sidebar_return_from_map)

    def _connect_gallery_signals(self) -> None:
        """Grid, viewer and map interactions."""

        ui = self._host._window.ui

        # Grid interactions — double-click opens the detail view. A single click only
        # selects: opening on click made an accidental click eject the user into the
        # detail page.
        ui.grid_view.itemDoubleClicked.connect(
            self._host._gallery_interactions.on_asset_clicked
        )
        ui.grid_view.visibleRowsChanged.connect(self._host._asset_list_vm.prioritize_rows)
        ui.grid_view.visibleRowsChanged.connect(
            self._host._gallery_interactions.on_gallery_scrolled
        )

        # Background AI scans step aside while the user is actually looking at
        # something: leaving them running made viewing and scrolling sluggish,
        # because face and OCR inference read full-size images and hold CPU.
        self._host._view_router.detailViewShown.connect(
            self._host._gallery_interactions.enter_foreground_viewing
        )
        self._host._view_router.galleryViewShown.connect(
            self._host._gallery_interactions.leave_foreground_viewing
        )
        self._host._view_router.mapViewShown.connect(
            self._host._gallery_interactions.leave_foreground_viewing
        )
        map_view = self._host._view_router.map_view()
        if map_view is not None:
            signal = getattr(map_view, "userInteracting", None)
            if signal is not None:
                signal.connect(self._host._gallery_interactions.touch_foreground_viewing)
        self._host._view_router.peopleViewShown.connect(
            self._host._gallery_interactions.leave_foreground_viewing
        )

        # Filmstrip clicks are now handled by PlaybackCoordinator

        # Connect favorite click from grid view
        ui.grid_view.favoriteClicked.connect(
            self._host._gallery_interactions.on_favorite_clicked
        )
        # Coordinator Signals
        self._host._playback.assetChanged.connect(self._host._gallery_interactions.sync_selection)
        self._host._player_view_controller.imageLoadingFailed.connect(
            self._host._gallery_interactions.on_media_load_failed
        )
        ui.video_area.mediaLoadFailed.connect(
            self._host._gallery_interactions.on_media_load_failed
        )

        # Viewer Interactions (Wheel Navigation)
        ui.image_viewer.nextItemRequested.connect(self._host._playback.select_next)
        ui.image_viewer.prevItemRequested.connect(self._host._playback.select_previous)
        ui.video_area.nextItemRequested.connect(self._host._playback.select_next)
        ui.video_area.prevItemRequested.connect(self._host._playback.select_previous)

        # ⭐ 视频播放状态跟踪：连接到runtime controller以动态调整人脸扫描频率
        ui.video_area.playbackStateChanged.connect(
            self._host._gallery_interactions.on_video_playback_state_changed
        )

        # Prev / Next overlay buttons
        ui.detail_page.prev_button.clicked.connect(self._host._playback.select_previous)
        ui.detail_page.next_button.clicked.connect(self._host._playback.select_next)
        # Map view cluster interactions
        ui.map_view.assetActivated.connect(self._host._on_map_asset_activated)
        ui.map_view.clusterActivated.connect(self._host._on_cluster_activated)
        ui.map_view.showAllRequested.connect(self._host._handle_map_show_all_requested)
        # Map feature buttons (in map source bar, to the right of map selector)
        ui.map_view.btn_person_filter.clicked.connect(self._host._location.toggle_person_filter)
        ui.map_view.btn_trail_toggle.clicked.connect(self._host._location.toggle_trail)

    def _connect_menu_signals(self) -> None:
        """Menu actions, buttons and the AI status surfaces."""

        ui = self._host._window.ui

        # Menus
        ui.open_album_action.triggered.connect(
            self._host._library_open.handle_open_album_dialog
        )
        ui.rescan_action.triggered.connect(self._host._status_bar.begin_scan)
        ui.rescan_action.triggered.connect(self._host._gallery_vm.rescan_current)
        ui.download_map_extension_action.triggered.connect(
            lambda: self._host._map_extension_download.start_download(source="settings")
        )
        ui.edit_button.clicked.connect(self._host._detail_vm.request_edit)
        # ui.edit_rotate_left_button is handled by EditCoordinator in Edit Mode
        ui.rotate_left_button.clicked.connect(self._host._playback.rotate_current_asset)
        ui.favorite_button.clicked.connect(self._host._detail_vm.toggle_favorite)
        ui.toggle_face_names_action.toggled.connect(
            self._host._window_preferences.on_face_name_toggle_changed
        )
        ui.toggle_hidden_people_action.toggled.connect(
            self._host._window_preferences.on_hidden_people_toggle_changed
        )

        # Semantic Search
        ui.main_header.search_submitted_with_scope.connect(
            self._host._search.handle_search_requested
        )
        ui.main_header.search_popup_opened.connect(self._host._search.prepare_search_popup)
        ui.main_header.similar_search_requested.connect(self._host._search.handle_similar_search)
        ui.main_header.search_history_clear_requested.connect(
            self._host._search.clear_search_history
        )
        self._host._thumbnail_service.thumbnailReady.connect(
            self._host._search.on_popup_thumbnail_ready
        )
        ui.main_header.search_precision_group.triggered.connect(
            self._host._search.handle_search_precision_changed
        )
        ui.sidebar.searchEntrySelected.connect(self._host._search.handle_search_entry_selected)
        ui.main_header.toggle_semantic_search_action.toggled.connect(
            self._host._handle_semantic_search_toggle
        )
        # Agent Organize Features
        ui.main_header.find_duplicates_action.triggered.connect(
            self._host._organize.find_duplicates
        )
        ui.main_header.smart_album_event_action.triggered.connect(
            lambda: self._host._organize.create_smart_albums("event")
        )
        ui.main_header.smart_album_location_action.triggered.connect(
            lambda: self._host._organize.create_smart_albums("location")
        )
        ui.main_header.smart_album_time_action.triggered.connect(
            lambda: self._host._organize.create_smart_albums("time")
        )
        ui.main_header.smart_album_theme_action.triggered.connect(
            lambda: self._host._organize.create_smart_albums("theme")
        )
        # Info Button
        ui.info_button.clicked.connect(self._host._playback.toggle_info_panel)
        # Back Button (detail page)
        ui.back_button.clicked.connect(self._host._detail_vm.back_to_gallery)
        # Fullscreen Button — already connected in window_manager._configure_window_controls

        # Map Button (detail page)
        ui.map_button.clicked.connect(self._host._toggle_detail_map)
        # Gallery page back button for cluster gallery mode
        ui.gallery_page.backRequested.connect(self._host._gallery_vm.return_from_cluster_gallery)
        # Search back button
        ui.gallery_page.searchBackRequested.connect(self._host._search.on_search_back)
        # Dashboard Click
        ui.albums_dashboard_page.albumSelected.connect(
            self._host._library_open.open_album_from_path
        )
        ui.people_page.clusterActivated.connect(
            self._host._people_navigation.on_cluster_activated
        )
        ui.people_page.groupActivated.connect(
            self._host._people_navigation.on_group_activated
        )
        self._host._context.library.peopleIndexUpdated.connect(ui.people_page.schedule_index_refresh)
        self._host._context.library.peopleSnapshotCommitted.connect(
            self._host._gallery_vm.handle_people_snapshot_committed
        )
        self._host._context.library.peopleSnapshotCommitted.connect(
            self._host._playback.handle_people_snapshot_committed
        )
        self._host._context.library.faceScanStatusChanged.connect(ui.people_page.set_status_message)
        # Also surface face scan progress in the main status bar so the
        # user can see it without switching to the People dashboard.
        self._host._context.library.faceScanStatusChanged.connect(
            lambda msg: self._host._status_bar.show_message(msg)
        )
        self._host._context.library.ocrScanStatusChanged.connect(
            self._host._search.on_ocr_scan_status
        )
        # Sidebar checkbox filtering
        ui.sidebar.albumCheckStateChanged.connect(
            self._host._window_preferences.on_album_check_state_changed
        )
        self._host._window_preferences.restore_checked_library_paths()

    def _connect_navigation_signals(self) -> None:
        """Navigation entry points and stored preferences."""

        ui = self._host._window.ui

        # Navigation
        self._host._navigation.bindLibraryRequested.connect(self._host._dialog.bind_library_dialog)
        ui.change_workspace_action.triggered.connect(
            self._host._library_open.change_workspace
        )
        ui.run_setup_action.triggered.connect(self._host._library_open.run_setup_wizard)
        ui.bind_library_action.triggered.connect(self._host._dialog.bind_library_dialog)
        self._host._detail_vm.edit_requested.connect(self._host._edit.enter_edit_mode)

        # Preferences (Wheel, Volume) - Filmstrip handled in PlaybackCoordinator
        self._host._window_preferences.restore()
        ui.wheel_action_group.triggered.connect(
            self._host._window_preferences.on_wheel_action_changed
        )

    def _connect_status_bar_signals(self) -> None:
        """Chrome wiring plus the search-model/embedding sync."""

        self._host._chrome.connect()

        # Semantic search needs a CLIP index; without it every search fails with
        # "no index found". Generation is expensive, so it runs once per session and
        # only when the repository reports missing embeddings.
        self._host._facade.scanFinished.connect(lambda *_: self._host._ensure_embedding_index())

        # The search model can be changed from the wizard or the settings window;
        # both only write the setting, and this keeps the runtime in sync.
        from iPhoto.agent.services import search_models as _search_models

        self._host._context.settings.settingsChanged.connect(
            self._host._on_search_model_setting_changed
        )
        self._host._context.model_selection.apply(
            "search", self._host._context.settings.get(_search_models.SETTINGS_KEY, None)
        )
