"""Coordinator that binds detail widgets to DetailViewModel presentation."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from ...i18n import tr

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QLocale, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPalette

from iPhoto.application.ports import EditServicePort, MapRuntimePort
from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.gui.ui.tasks.assign_location_worker import (
    AssignLocationRequest,
    AssignLocationWorker,
)
from iPhoto.infrastructure.services.map_runtime_service import SessionMapRuntimeService
from iPhoto.gui.detail_profile import log_detail_profile
from .playback_info import PlaybackInfoMixin
from .playback_faces import PlaybackFaceMixin
from .playback_render import PlaybackRenderMixin
from .playback_location import PlaybackLocationMixin
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.icons import load_icon
from iPhoto.gui.ui.tasks.info_panel_metadata_worker import (
    InfoPanelMetadataResult,
    InfoPanelMetadataWorker,
)
from iPhoto.gui.ui.tasks.manual_face_add_worker import ManualFaceAddWorker
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.info_panel import InfoPanel
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation, DetailViewModel
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.people.repository import AssetFaceAnnotation
from iPhoto.people.service import PeopleService
from maps.osmand_search import OsmAndSearchService, SearchSuggestion
from ...utils.env import is_truthy

if TYPE_CHECKING:
    from ...bootstrap.library_session import LibrarySession
    from iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter
    from iPhoto.gui.ui.widgets.face_name_overlay import FaceNameOverlayWidget
    from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter

LOGGER = logging.getLogger(__name__)

from .playback_constants import (  # noqa: F401 - re-exported for tests/callers
    _INFO_PANEL_METADATA_CACHE_MAX,
    _LOCATION_EXTENSION_PROMPT,
    _LOCATION_EXIFTOOL_LIMITED_MESSAGE,
    _LOCATION_EXIFTOOL_LIMITED_TITLE,
    _LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE,
    _LOCATION_FILE_WRITE_LIMITED_TITLE,
    _LOCATION_SEARCH_DEBOUNCE_MS,
    _LOCATION_SEARCH_RESULT_LIMIT,
)


class PlaybackCoordinator(
    PlaybackLocationMixin,
    PlaybackInfoMixin,
    PlaybackRenderMixin,
    PlaybackFaceMixin,
    QObject,
):
    """Bind detail widgets to the current presentation from DetailViewModel."""

    assetChanged = Signal(int)
    #: Delivered from the metadata worker thread; Qt queues it onto the GUI thread.
    #: `QTimer.singleShot` cannot be used from a worker thread: the timer belongs
    #: to that thread's event loop, which never runs, so the callback was dropped.
    aiMetadataReady = Signal(str, object, object)

    def __init__(
        self,
        player_bar: PlayerBar,
        player_view: PlayerViewController,
        router: ViewRouter,
        asset_model: GalleryListModelAdapter,
        detail_vm: DetailViewModel,
        adjustment_committer: MediaAdjustmentCommitter,
        zoom_slider: QSlider,
        zoom_in_button: QToolButton,
        zoom_out_button: QToolButton,
        zoom_widget: QWidget,
        favorite_button: QToolButton,
        info_button: QToolButton,
        rotate_button: QToolButton,
        edit_button: QPushButton,
        share_button: QToolButton,
        filmstrip_view: FilmstripView,
        toggle_filmstrip_action: QAction,
        settings: Settings,
        header_controller: HeaderController | None = None,
        face_name_overlay: FaceNameOverlayWidget | None = None,
        people_service: PeopleService | None = None,
        people_dashboard_refresh_callback: Callable[[], None] | None = None,
        library_manager: LibraryRuntimeController | None = None,
        location_session_invalidator: Callable[[], None] | None = None,
        library_session_getter: Callable[[], "LibrarySession | None"] | None = None,
        map_runtime: MapRuntimePort | None = None,
    ) -> None:
        super().__init__()
        self._player_bar = player_bar
        self._player_view = player_view
        self._router = router
        self._asset_model = asset_model
        self._detail_vm = detail_vm
        self._adjustment_committer = adjustment_committer

        self._zoom_slider = zoom_slider
        self._zoom_in = zoom_in_button
        self._zoom_out = zoom_out_button
        self._zoom_widget = zoom_widget

        self._favorite_button = favorite_button
        self._info_button = info_button
        self._rotate_button = rotate_button
        self._edit_button = edit_button
        self._share_button = share_button

        self._filmstrip_view = filmstrip_view
        self._toggle_filmstrip_action = toggle_filmstrip_action
        self._settings = settings
        self._header_controller = header_controller
        self._face_name_overlay = face_name_overlay
        self._people_service = people_service or PeopleService()
        self._people_dashboard_refresh_callback = people_dashboard_refresh_callback
        self._library_manager = library_manager
        self._location_session_invalidator = location_session_invalidator
        # The info panel shows AI captions/tags, which live in the current library
        # session. The session is created lazily when an album opens, so the
        # coordinator receives a getter instead of a value.
        self._library_session_getter = library_session_getter
        self._map_runtime = map_runtime or getattr(library_manager, "map_runtime", None)

        self._is_playing = False
        self._navigation: NavigationCoordinator | None = None
        self._info_panel: InfoPanel | None = None
        self._detail_map_panel = None
        self._active_live_motion: Path | None = None
        self._active_live_still: Path | None = None
        self._resume_after_transition = False
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_presentation: DetailPresentation | None = None
        self._info_panel_metadata_cache: dict[str, dict[str, Any]] = {}
        self._info_panel_metadata_inflight: set[str] = set()
        self._info_panel_metadata_attempted: set[str] = set()
        self._play_profile_started_at: float | None = None
        self._play_profile_row: int | None = None
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id: str | None = None
        self._pending_manual_face_annotations: dict[str, list[AssetFaceAnnotation]] = {}
        self._pending_manual_face_sequence = 0
        self._location_search_service: OsmAndSearchService | None = None
        self._location_search_cache: dict[str, list[SearchSuggestion]] = {}
        self._location_assign_inflight = False
        self._location_assign_path: Path | None = None
        self._location_preview_path: Path | None = None
        self._location_preview_metadata: dict[str, Any] | None = None
        self._pending_location_query = ""
        self._location_search_target_path: Path | None = None

        self._pending_play_row: int | None = None
        self._show_face_names = False
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(PLAY_ASSET_DEBOUNCE_MS)
        self._play_debounce.timeout.connect(self._execute_pending_play)
        self._location_search_timer = QTimer(self)
        self._location_search_timer.setSingleShot(True)
        self._location_search_timer.setInterval(_LOCATION_SEARCH_DEBOUNCE_MS)
        self._location_search_timer.timeout.connect(self._perform_location_search)

        self.aiMetadataReady.connect(self._apply_info_panel_ai_metadata)
        self._connect_signals()
        self._setup_zoom_handler()
        self._restore_filmstrip_preference()

    def set_navigation_coordinator(self, nav: NavigationCoordinator) -> None:
        self._navigation = nav

    def set_people_service(self, service: PeopleService | None) -> None:
        self._people_service = service or PeopleService()
        self._refresh_face_name_overlay_for_current_presentation()

    def set_info_panel(self, panel: InfoPanel) -> None:
        self._info_panel = panel
        panel.dismissed.connect(self._handle_info_panel_dismissed)
        panel.manualFaceAddRequested.connect(self._handle_manual_face_add_requested)
        panel.faceDeleteRequested.connect(self._handle_info_panel_face_delete_requested)
        panel.faceMoveRequested.connect(self._handle_info_panel_face_move_requested)
        panel.faceMoveToNewPersonRequested.connect(
            self._handle_info_panel_face_move_to_new_person_requested
        )
        panel.locationQueryChanged.connect(self._handle_location_query_changed)
        panel.locationConfirmRequested.connect(self._handle_location_confirm_requested)

    def set_people_library_root(self, library_root: Path | None) -> None:
        people_service = self._people_service
        service_matches_root = (
            isinstance(people_service, PeopleService)
            and people_service.library_root() == library_root
        )
        if not service_matches_root:
            bound_people_service = getattr(self._library_manager, "people_service", None)
            if (
                isinstance(bound_people_service, PeopleService)
                and bound_people_service.library_root() == library_root
            ):
                self._people_service = bound_people_service
            elif library_root is None:
                self._people_service = PeopleService()
            else:
                self._people_service = PeopleService(library_root)
        self._refresh_face_name_overlay_for_current_presentation()

    def set_map_runtime(self, map_runtime: MapRuntimePort | None) -> None:
        """Bind the current session map runtime capability surface."""

        previous_package_root = self._map_runtime_package_root()
        self._map_runtime = map_runtime or getattr(self._library_manager, "map_runtime", None)
        if self._map_runtime_package_root() != previous_package_root:
            self._reset_location_search_service()
        if self._info_panel is None or self._info_panel.current_rel() is None:
            return
        capabilities = self._map_runtime_capabilities()
        self._info_panel.set_location_capability(
            enabled=self._refresh_location_extension_state(),
            preview_enabled=self._info_panel_preview_enabled(capabilities),
            fallback_text=tr(_LOCATION_EXTENSION_PROMPT),
        )

    def set_face_name_display_enabled(self, enabled: bool) -> None:
        self._show_face_names = bool(enabled)
        self._refresh_face_name_overlay_for_current_presentation()

    def set_detail_map_panel(self, panel) -> None:
        """Bind the detail map panel for GPS location display."""
        self._detail_map_panel = panel
        if panel is not None:
            panel.set_map_runtime(self._map_runtime)

    def current_row(self) -> int:
        row = self._detail_vm.current_row.value
        return int(row) if isinstance(row, int) else -1

    def suspend_playback_for_transition(self) -> bool:
        resume_after = self._is_playing
        self._resume_after_transition = resume_after
        if resume_after:
            self._player_view.video_area.pause()
        return resume_after

    def resume_playback_after_transition(self) -> None:
        if not self._resume_after_transition:
            return
        self._resume_after_transition = False
        self._player_view.video_area.play()

    def prepare_fullscreen_asset(self) -> bool:
        if self._asset_model.rowCount() <= 0:
            return False
        current_row = self.current_row()
        target_row = current_row if current_row >= 0 else 0
        if current_row < 0 or not self._router.is_detail_view_active():
            self.play_asset(target_row)
        return True

    def show_placeholder_in_viewer(self) -> None:
        self._player_view.show_placeholder()
        self._hide_face_name_overlay(clear_annotations=True)

    def _connect_signals(self) -> None:
        self._player_bar.playPauseRequested.connect(self.toggle_playback)
        self._player_bar.scrubStarted.connect(self._on_scrub_start)
        self._player_bar.scrubFinished.connect(self._on_scrub_end)
        self._player_bar.seekRequested.connect(self._on_seek)

        self._player_view.liveReplayRequested.connect(self.replay_live_photo)
        self._player_view.video_area.playbackStateChanged.connect(self._sync_playback_state)
        self._player_view.video_area.playbackFinished.connect(self._handle_playback_finished)
        self._player_view.video_area.durationChanged.connect(self._on_video_duration_changed)
        self._player_view.video_area.positionChanged.connect(self._on_video_position_changed)

        self._detail_vm.route_requested.connect(self._handle_route_requested)
        self._detail_vm.presentation_changed.connect(self._handle_presentation_changed)
        self._detail_vm.rotate_requested.connect(self._handle_rotate_requested)
        self._detail_vm.edit_requested.connect(self._handle_edit_requested)

        self._filmstrip_view.nextItemRequested.connect(self.select_next)
        self._filmstrip_view.prevItemRequested.connect(self.select_previous)
        self._filmstrip_view.itemClicked.connect(self._on_filmstrip_clicked)
        self._toggle_filmstrip_action.toggled.connect(self._handle_filmstrip_toggled)
        rename_signal = getattr(self._face_name_overlay, "renameSubmitted", None)
        if rename_signal is not None:
            rename_signal.connect(self._handle_face_name_rename_submitted)
        manual_signal = getattr(self._face_name_overlay, "manualFaceSubmitted", None)
        if manual_signal is not None:
            manual_signal.connect(self._handle_manual_face_submitted)

    def _setup_zoom_handler(self) -> None:
        self._zoom_handler = EditZoomHandler(
            viewer=self._player_view.image_viewer,
            zoom_in_button=self._zoom_in,
            zoom_out_button=self._zoom_out,
            zoom_slider=self._zoom_slider,
            parent=self,
        )
        self._zoom_handler.connect_controls()

    def _restore_filmstrip_preference(self) -> None:
        show = is_truthy(self._settings.get("ui.show_filmstrip", True))
        self._filmstrip_view.setVisible(show)
        self._toggle_filmstrip_action.setChecked(show)

    @Slot(bool)
    def _handle_filmstrip_toggled(self, checked: bool) -> None:
        self._filmstrip_view.setVisible(checked)
        self._settings.set("ui.show_filmstrip", checked)

    @Slot(QModelIndex)
    def _on_filmstrip_clicked(self, index: QModelIndex) -> None:
        model = self._filmstrip_view.model()
        if hasattr(model, "mapToSource"):
            source_idx = model.mapToSource(index)
            if source_idx.isValid():
                self.play_asset(source_idx.row())
                return
        self.play_asset(index.row())

    @Slot(int)
    @Slot()
    def toggle_playback(self) -> None:
        if self._is_playing:
            self._player_view.video_area.pause()
        else:
            self._player_view.video_area.play()

    @Slot(bool)
    def _sync_playback_state(self, is_playing: bool) -> None:
        self._is_playing = is_playing

    @Slot()
    def _on_scrub_start(self) -> None:
        self._player_view.video_area.pause()

    @Slot()
    def _on_scrub_end(self) -> None:
        if self._is_playing:
            self._player_view.video_area.play()

    @Slot(int)
    def _on_seek(self, position: int) -> None:
        self._player_view.video_area.seek(position + self._trim_in_ms)

    @Slot(int)
    def _on_video_duration_changed(self, duration_ms: int) -> None:
        if self._player_view.video_area.is_edit_mode_active():
            return
        trim_in_ms, trim_out_ms = self._player_view.video_area.trim_range_ms()
        self._trim_in_ms = trim_in_ms
        self._trim_out_ms = trim_out_ms
        if self._trim_out_ms > self._trim_in_ms:
            self._player_bar.set_duration(self._trim_out_ms - self._trim_in_ms)
        else:
            self._player_bar.set_duration(duration_ms)

    @Slot(int)
    def _on_video_position_changed(self, position_ms: int) -> None:
        if self._player_view.video_area.is_edit_mode_active():
            return
        self._player_bar.set_position(max(0, position_ms - self._trim_in_ms))

    def play_asset(self, row: int) -> None:
        try:
            if row < 0 or row >= self._asset_model.rowCount():
                return
        except Exception:
            logging.getLogger(__name__).exception("play_asset bounds check failed for row %s", row)
            return
        self._play_profile_started_at = time.perf_counter()
        self._play_profile_row = row
        if not self._play_debounce.isActive() and self._pending_play_row is None:
            self._dispatch_play_row(row, reason="immediate")
            self._play_debounce.start()
            return
        self._pending_play_row = row
        if not self._play_debounce.isActive():
            self._play_debounce.start()

    def _execute_pending_play(self) -> None:
        row = self._pending_play_row
        self._pending_play_row = None
        if row is None:
            return
        self._dispatch_play_row(row, reason="debounced")
        self._play_debounce.start()

    def _clear_play_profile(self, row: int | None = None) -> None:
        if row is not None and self._play_profile_row != row:
            return
        self._play_profile_started_at = None
        self._play_profile_row = None

    def _clear_play_request_state(self) -> None:
        self._pending_play_row = None
        self._clear_play_profile()
        play_debounce = self._play_debounce
        if play_debounce is not None:
            play_debounce.stop()

    def _dispatch_play_row(self, row: int, *, reason: str) -> None:
        if (
            self._play_profile_started_at is not None
            and self._play_profile_row == row
        ):
            elapsed_ms = (time.perf_counter() - self._play_profile_started_at) * 1000.0
            log_detail_profile(
                "playback",
                "play_asset.dispatch",
                elapsed_ms,
                row=row,
                reason=reason,
            )
        try:
            self._detail_vm.show_row(row)
        except Exception:
            logging.getLogger(__name__).exception(
                "show_row failed for row %s (reason=%s)", row, reason
            )

    @Slot(str)
    def _handle_route_requested(self, view: str) -> None:
        if view == "detail":
            self._router.show_detail()
        elif view == "gallery":
            self.reset_for_gallery()
            self._router.show_gallery()

    @Slot(object)
    def _handle_edit_requested(self, _path: object) -> None:
        self._hide_face_name_overlay(clear_annotations=False)





















    _FILMSTRIP_SYNC_MAX_RETRIES = 5
    _FILMSTRIP_SYNC_RETRY_MS = 50

    def _sync_filmstrip_selection(self, row: int) -> None:
        self._do_sync_filmstrip(row, attempt=0)

    def _do_sync_filmstrip(self, row: int, attempt: int = 0) -> None:
        try:
            model = self._filmstrip_view.model()
            if model is None:
                return
            row_count = model.rowCount()
            if row_count == 0:
                if attempt < self._FILMSTRIP_SYNC_MAX_RETRIES:
                    QTimer.singleShot(
                        self._FILMSTRIP_SYNC_RETRY_MS,
                        lambda: self._do_sync_filmstrip(row, attempt + 1),
                    )
                return
            idx = self._asset_model.index(row, 0)
            if hasattr(model, "mapFromSource"):
                idx = model.mapFromSource(idx)
            if not idx.isValid():
                if attempt < self._FILMSTRIP_SYNC_MAX_RETRIES:
                    QTimer.singleShot(
                        self._FILMSTRIP_SYNC_RETRY_MS,
                        lambda: self._do_sync_filmstrip(row, attempt + 1),
                    )
                return
            # Re-validate against current model state to avoid stale indices.
            if idx.row() < 0 or idx.row() >= row_count:
                return
            self._filmstrip_view.selectionModel().setCurrentIndex(
                idx, QItemSelectionModel.ClearAndSelect
            )
            self._filmstrip_view.center_on_index(idx)
        except Exception:
            logging.getLogger(__name__).debug(
                "_do_sync_filmstrip failed for row %s", row, exc_info=True
            )

    def _update_favorite_icon(self, is_favorite: bool) -> None:
        icon_name = "suit.heart.fill.svg" if is_favorite else "suit.heart.svg"
        icon_color = self._resolve_icon_tint()
        self._favorite_button.setIcon(load_icon(icon_name, color=icon_color))

    def _resolve_icon_tint(self) -> str | None:
        palette = self._favorite_button.palette()
        color = palette.color(QPalette.ColorRole.ButtonText)
        if not color.isValid():
            color = palette.color(QPalette.ColorRole.WindowText)
        if not color.isValid():
            return None
        return color.name(QColor.NameFormat.HexArgb)

    def reset_for_gallery(self) -> None:
        self._clear_play_request_state()
        location_timer = self._location_search_timer
        if location_timer is not None:
            location_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        location_service = self._location_search_service
        if location_service is not None:
            location_service.shutdown()
            self._location_search_service = None
        self._player_view.video_area.stop()
        self._player_view.show_placeholder()
        self._hide_face_name_overlay(clear_annotations=True)
        self._player_bar.setEnabled(False)
        self._is_playing = False
        self._current_presentation = None
        self._detail_vm.hide_info_panel(refresh_presentation=False)
        self._update_header(None)
        if self._info_panel:
            self._info_panel.close()
        self._clear_info_panel_metadata_state()

    def shutdown(self) -> None:
        self._clear_play_request_state()
        self._location_search_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        if self._location_search_service is not None:
            self._location_search_service.shutdown()
            self._location_search_service = None
        self._player_view.video_area.stop()
        self._hide_face_name_overlay(clear_annotations=True)
        self._is_playing = False
        self._current_presentation = None
        self._detail_vm.hide_info_panel(refresh_presentation=False)
        self._update_header(None)
        if self._info_panel:
            self._info_panel.shutdown()
            self._info_panel.close()
        self._clear_info_panel_metadata_state()

    def _update_header(self, presentation: DetailPresentation | None) -> None:
        if not self._header_controller:
            return
        if presentation is None:
            self._header_controller.clear()
            return
        self._header_controller.update_from_values(presentation.location, presentation.timestamp)

    def _edit_service(self) -> EditServicePort | None:
        library_manager = self._library_manager
        if library_manager is None:
            return None
        return getattr(library_manager, "edit_service", None)

    def select_next(self) -> None:
        self._detail_vm.next()

    def select_previous(self) -> None:
        self._detail_vm.previous()

    def replay_live_photo(self) -> None:
        presentation = self._current_presentation
        if presentation is None or not presentation.is_live:
            return
        self._autoplay_live_motion(presentation)

    def rotate_current_asset(self) -> None:
        self._detail_vm.rotate_current()

    def _handle_rotate_requested(self, path: object, is_video: object) -> None:
        if not isinstance(path, Path):
            return
        is_video_value = bool(is_video)
        if is_video_value:
            updates = self._player_view.video_area.rotate_image_ccw()
        else:
            updates = self._player_view.image_viewer.rotate_image_ccw()
        try:
            edit_service = self._edit_service()
            if edit_service is None:
                raise RuntimeError("Edit service is unavailable")
            current_adjustments = edit_service.read_adjustments(path)
            current_adjustments.update(updates)
            self._adjustment_committer.commit(path, current_adjustments, reason="rotate")
        except Exception:
            LOGGER.exception("Failed to rotate %s", path)













































