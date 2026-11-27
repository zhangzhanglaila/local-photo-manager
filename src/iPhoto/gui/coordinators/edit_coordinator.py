"""Coordinator for the Edit View workflow."""

from __future__ import annotations
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import QObject, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from iPhoto.application.ports import EditServicePort
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.events.bus import EventBus
from iPhoto.gui.ui.models.edit_session import EditSession
from iPhoto.gui.ui.controllers.edit_history_manager import EditHistoryManager
from iPhoto.gui.ui.controllers.edit_pipeline_loader import EditPipelineLoader
from iPhoto.gui.ui.controllers.edit_preview_manager import EditPreviewManager
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.edit_modes import AdjustModeState, CropModeState
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.controllers.edit_fullscreen_manager import EditFullscreenManager
from iPhoto.gui.ui.controllers.edit_view_transition import EditViewTransitionManager
from iPhoto.gui.ui.tasks.video_trim_thumbnail_worker import VideoTrimThumbnailWorker
from iPhoto.gui.ui.tasks.video_sidebar_preview_worker import (
    VideoSidebarPreviewResult,
    VideoSidebarPreviewWorker,
)
from iPhoto.gui.ui.controllers.edit_preview_manager import resolve_adjustment_mapping
from iPhoto.gui.ui.palette import viewer_surface_color
from iPhoto.gui.ui.media import MediaRestoreRequest
from iPhoto.io.metadata import read_video_meta
from iPhoto.media_classifier import VIDEO_EXTENSIONS
from iPhoto.utils.logging import get_logger
from iPhoto.utils.ffmpeg import probe_media, video_rotation_from_meta
from iPhoto.core.adjustment_mapping import (
    VIDEO_TRIM_IN_KEY,
    VIDEO_TRIM_OUT_KEY,
    normalise_video_trim,
)
from iPhoto.core.curve_resolver import DEFAULT_CURVE_POINTS
from iPhoto.core.levels_resolver import DEFAULT_LEVELS_HANDLES
from iPhoto.core.selective_color_resolver import DEFAULT_SELECTIVE_COLOR_RANGES
from ...utils.logging import diag

if TYPE_CHECKING:
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
    from iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController
    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter, MediaSelectionSession

_LOGGER = logging.getLogger(__name__)

from .edit_adjustments import EditAdjustmentMixin
from .edit_video import EditVideoMixin
from .edit_support import (  # noqa: F401 - re-exported for existing callers
    _APP_LOGGER,
    _DEFAULT_VIDEO_FRAME_STEP_MS,
    _video_trim_thumbnail_count,
)


class EditCoordinator(EditVideoMixin, EditAdjustmentMixin, QObject):
    """
    Manages the Edit View, including entering/exiting edit mode and applying changes.
    Replaces EditController.
    """

    def __init__(
        self,
        edit_page: QObject, # The widget containing edit UI (Ui_MainWindow components)
        router: ViewRouter,
        event_bus: EventBus,
        asset_vm: GalleryListModelAdapter,
        window: QObject | None = None,
        theme_controller: WindowThemeController | None = None,
        navigation: "NavigationCoordinator | None" = None,
        media_session: "MediaSelectionSession | None" = None,
        adjustment_committer: "MediaAdjustmentCommitter | None" = None,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
    ):
        super().__init__()
        # We need access to specific UI elements within edit_page (which is likely MainWindow.ui)
        self._ui = edit_page
        self._router = router
        self._bus = event_bus
        self._asset_vm = asset_vm
        self._theme_controller = theme_controller
        self._navigation = navigation
        self._media_session = media_session
        self._adjustment_committer = adjustment_committer
        self._edit_service_getter = edit_service_getter

        self._transition_manager = EditViewTransitionManager(
            self._ui,
            window,
            parent=self,
            theme_controller=theme_controller
        )

        # State
        self._session: Optional[EditSession] = None
        self._current_source: Optional[Path] = None
        self._history_manager = EditHistoryManager(parent=self)
        self._pipeline_loader = EditPipelineLoader(self)
        self._is_loading_edit_image = False
        self._skip_next_preview_frame = False
        self._compare_active = False
        self._active_adjustments: dict[str, float] = {}
        self._video_color_stats = None
        self._video_thumbnail_generation = 0
        self._video_sidebar_generation = 0
        self._video_trim_worker: VideoTrimThumbnailWorker | None = None
        self._video_sidebar_worker: VideoSidebarPreviewWorker | None = None
        self._video_sidebar_workers: list[VideoSidebarPreviewWorker] = []
        self._video_trim_diag: dict[int, dict[str, object]] = {}
        self._pending_video_duration_sec: float | None = None
        self._video_frame_step_ms = _DEFAULT_VIDEO_FRAME_STEP_MS
        # Rotation and coded size of the video currently loaded for trimming.
        # Recorded once when the clip is opened; the trim thumbnail estimator
        # must never call ffprobe from a widget signal slot.
        self._video_rotation_source: Path | None = None
        self._video_rotation_info: tuple[int, int, int] = (0, 0, 0)

        # Helpers / Sub-controllers (Ported from EditController)
        self._zoom_handler = EditZoomHandler(
            viewer=self._ui.edit_image_viewer,
            zoom_in_button=self._ui.zoom_in_button,
            zoom_out_button=self._ui.zoom_out_button,
            zoom_slider=self._ui.zoom_slider,
            parent=self,
        )

        self._adjust_mode = AdjustModeState(
            self._ui,
            lambda: self._session,
            lambda: self._active_edit_viewport(),
            parent=self,
        )
        self._crop_mode = CropModeState(
            self._ui,
            lambda: self._session,
            lambda: self._active_edit_viewport(),
            parent=self,
        )
        self._current_mode = self._adjust_mode

        # Create HeaderController with UI reference for layout management
        # Uses placeholder labels that won't be displayed - only layout management is used
        self._header_controller = HeaderController(
            self._ui.location_label,
            self._ui.timestamp_label,
            ui=self._ui,
            parent=self,
        )
        self._preview_manager = EditPreviewManager(self._ui.edit_image_viewer, self)
        self._fullscreen_manager = EditFullscreenManager(
            self._ui,
            window,
            self._preview_manager,
            parent=self,
        )

        self._update_throttler = QTimer(self)
        self._update_throttler.setSingleShot(True)
        self._update_throttler.setInterval(30)  # ~30fps cap
        self._update_throttler.timeout.connect(self._perform_deferred_update)
        self._video_trim_thumbnail_timer = QTimer(self)
        self._video_trim_thumbnail_timer.setSingleShot(True)
        self._video_trim_thumbnail_timer.setInterval(1500)
        self._video_trim_thumbnail_timer.timeout.connect(self._flush_video_trim_thumbnail_request)
        self._video_sidebar_preview_timer = QTimer(self)
        self._video_sidebar_preview_timer.setSingleShot(True)
        self._video_sidebar_preview_timer.setInterval(1000)
        self._video_sidebar_preview_timer.timeout.connect(self._queue_video_sidebar_preview)
        self._pending_session_values: Optional[dict] = None
        self._preview_updates_suspended = False
        self._interaction_depth = 0
        self._eyedropper_target = "curve"

        self._connect_signals()

    def _connect_signals(self):
        # Pipeline signals
        self._pipeline_loader.imageLoaded.connect(self._on_edit_image_loaded)
        self._pipeline_loader.imageLoadFailed.connect(self._on_edit_image_load_failed)
        self._pipeline_loader.sidebarPreviewReady.connect(self._handle_sidebar_preview_ready)

        # UI wiring
        self._ui.edit_reset_button.clicked.connect(self._handle_reset_clicked)
        self._ui.edit_done_button.clicked.connect(self._handle_done_clicked)
        self._ui.edit_rotate_left_button.clicked.connect(self._handle_rotate_left_clicked)

        # Mode switching
        self._ui.edit_adjust_action.triggered.connect(lambda c: self._handle_mode_change("adjust", c))
        self._ui.edit_crop_action.triggered.connect(lambda c: self._handle_mode_change("crop", c))
        self._ui.edit_mode_control.currentIndexChanged.connect(self._handle_top_bar_index_changed)

        # Compare
        self._ui.edit_compare_button.pressed.connect(self._handle_compare_pressed)
        self._ui.edit_compare_button.released.connect(self._handle_compare_released)

        # Sidebar interactions
        self._ui.edit_sidebar.interactionStarted.connect(self.push_undo_state)
        self._ui.edit_sidebar.interactionStarted.connect(self._handle_sidebar_interaction_started)
        self._ui.edit_sidebar.interactionFinished.connect(self._handle_sidebar_interaction_finished)
        self._ui.edit_sidebar.bwParamsPreviewed.connect(self._handle_bw_params_previewed)
        self._ui.edit_sidebar.bwParamsCommitted.connect(self._handle_bw_params_committed)
        self._ui.edit_sidebar.wbParamsPreviewed.connect(self._handle_wb_params_previewed)
        self._ui.edit_sidebar.wbParamsCommitted.connect(self._handle_wb_params_committed)
        self._ui.edit_sidebar.curveParamsPreviewed.connect(self._handle_curve_params_previewed)
        self._ui.edit_sidebar.curveParamsCommitted.connect(self._handle_curve_params_committed)
        self._ui.edit_sidebar.levelsParamsPreviewed.connect(self._handle_levels_params_previewed)
        self._ui.edit_sidebar.levelsParamsCommitted.connect(self._handle_levels_params_committed)
        self._ui.edit_sidebar.definitionParamsPreviewed.connect(
            self._handle_definition_params_previewed
        )
        self._ui.edit_sidebar.definitionParamsCommitted.connect(
            self._handle_definition_params_committed
        )
        self._ui.edit_sidebar.denoiseParamsPreviewed.connect(
            self._handle_denoise_params_previewed
        )
        self._ui.edit_sidebar.denoiseParamsCommitted.connect(
            self._handle_denoise_params_committed
        )
        self._ui.edit_sidebar.sharpenParamsPreviewed.connect(
            self._handle_sharpen_params_previewed
        )
        self._ui.edit_sidebar.sharpenParamsCommitted.connect(
            self._handle_sharpen_params_committed
        )
        self._ui.edit_sidebar.vignetteParamsPreviewed.connect(
            self._handle_vignette_params_previewed
        )
        self._ui.edit_sidebar.vignetteParamsCommitted.connect(
            self._handle_vignette_params_committed
        )
        self._ui.edit_sidebar.selectiveColorParamsPreviewed.connect(
            self._handle_selective_color_params_previewed
        )
        self._ui.edit_sidebar.selectiveColorParamsCommitted.connect(
            self._handle_selective_color_params_committed
        )
        self._ui.edit_sidebar.curveEyedropperModeChanged.connect(
            self._handle_curve_eyedropper_mode_changed
        )
        self._ui.edit_sidebar.wbEyedropperModeChanged.connect(
            self._handle_wb_eyedropper_mode_changed
        )
        self._ui.edit_sidebar.selectiveColorEyedropperModeChanged.connect(
            self._handle_selective_color_eyedropper_mode_changed
        )
        self._ui.edit_sidebar.perspectiveInteractionStarted.connect(
            lambda: self._active_edit_viewport().start_perspective_interaction()
        )
        self._ui.edit_sidebar.perspectiveInteractionFinished.connect(
            lambda: self._active_edit_viewport().end_perspective_interaction()
        )
        self._ui.edit_sidebar.aspectRatioChanged.connect(
            lambda ratio: self._active_edit_viewport().set_crop_aspect_ratio(ratio)
        )
        self._ui.edit_image_viewer.cropInteractionStarted.connect(self.push_undo_state)
        self._ui.edit_image_viewer.cropChanged.connect(self._handle_crop_changed)
        self._ui.edit_image_viewer.colorPicked.connect(self._handle_color_picked)
        self._ui.video_area.cropInteractionStarted.connect(self.push_undo_state)
        self._ui.video_area.cropChanged.connect(self._handle_crop_changed)
        self._ui.video_area.colorPicked.connect(self._handle_color_picked)
        self._ui.video_area.durationChanged.connect(self._handle_video_duration_changed)
        self._ui.video_area.positionChanged.connect(self._handle_video_position_changed)
        self._ui.video_area.playbackStateChanged.connect(self._handle_video_playback_state_changed)
        self._ui.video_trim_bar.playPauseRequested.connect(self._handle_video_trim_play_pause_requested)
        self._ui.video_trim_bar.inPointChanged.connect(self._handle_trim_in_ratio_changed)
        self._ui.video_trim_bar.outPointChanged.connect(self._handle_trim_out_ratio_changed)
        self._ui.video_trim_bar.playheadSeeked.connect(self._handle_trim_playhead_seeked)
        self._ui.video_trim_bar.trimDragStarted.connect(self.push_undo_state)
        self._ui.video_trim_bar.trimDragStarted.connect(self._ui.video_area.pause)

    def _active_edit_viewport(self):
        """Return the viewport currently driving the edit workflow."""

        if self._is_video_source():
            return self._ui.video_area
        return self._ui.edit_image_viewer

    def _is_video_source(self) -> bool:
        """Return ``True`` when the active edit source is a video."""

        return (
            self._current_source is not None
            and self._current_source.suffix.lower() in VIDEO_EXTENSIONS
        )

    def is_in_fullscreen(self) -> bool:
        """Return ``True`` if the edit view is in immersive full screen mode."""

        return self._fullscreen_manager.is_in_fullscreen()

    def is_editing(self) -> bool:
        """Return ``True`` when an edit session is active."""

        return self._session is not None

    # ------------------------------------------------------------------
    # Public transport API (used by AppShortcutManager)
    # ------------------------------------------------------------------

    def video_is_transport_active(self) -> bool:
        """Return True when video transport shortcuts should be handled here.

        The shortcut manager calls this to decide whether to route Space /
        frame-step commands to the edit coordinator rather than the plain
        detail-view handler.
        """
        return self._can_handle_video_edit_transport_shortcut(allow_conflicting_focus=True)

    def toggle_video_playback(self) -> None:
        """Toggle play / pause for the video currently open in edit mode."""
        self._handle_video_trim_play_pause_requested()

    def step_video_frame(self, direction: int) -> None:
        """Advance (direction=+1) or rewind (direction=-1) one frame."""
        self._handle_video_frame_step_shortcut(direction)

    def enter_fullscreen_preview(self) -> bool:
        """Enter immersive full screen preview for the current edit session."""

        if not self._session or not self._current_source:
            return False
        adjustments = self._resolve_session_adjustments()
        return self._fullscreen_manager.enter_fullscreen_preview(
            self._current_source,
            adjustments,
        )

    def exit_fullscreen_preview(self) -> None:
        """Exit immersive full screen preview if active."""

        source = self._current_source
        adjustments = None
        if self._session is not None:
            adjustments = self._resolve_session_adjustments()
        self._fullscreen_manager.exit_fullscreen_preview(source, adjustments)

    def enter_edit_mode(self, asset_path: Path):
        """Prepares the edit view for the given asset and switches view."""
        if self._session is not None:
            return
        _LOGGER.debug(
            "[trace][edit] enter_edit_mode:start %s",
            {
                "asset": str(asset_path),
                "is_video": asset_path.suffix.lower() in VIDEO_EXTENSIONS,
            },
        )

        self._current_source = asset_path
        self._emit_video_trim_diag(
            "enter_edit_mode",
            source=asset_path,
            is_video=asset_path.suffix.lower() in VIDEO_EXTENSIONS,
        )

        # Load Adjustments
        edit_service = self._edit_service()
        adjustments = (
            edit_service.read_adjustments(asset_path)
            if edit_service is not None
            else {}
        )

        # Setup Session
        session = EditSession(self)
        session.set_values(adjustments, emit_individual=False)
        session.valuesChanged.connect(self._handle_session_changed)
        self._session = session
        self._history_manager.set_session(session)
        self._ui.edit_sidebar.set_session(session)
        self._ui.edit_sidebar.set_video_edit_mode(self._is_video_source())

        viewport = self._active_edit_viewport()
        self._zoom_handler.set_viewer(viewport)
        self._ui.video_trim_bar.setVisible(self._is_video_source())

        # Apply to Viewer
        self._apply_session_adjustments_to_viewer()

        # Reset Viewer State
        viewport.setCropMode(False, session.values())
        if self._is_video_source():
            self._skip_next_preview_frame = False
            viewport.reset_zoom()
            self._start_video_edit_load(asset_path)
        else:
            viewer = self._ui.edit_image_viewer
            current_source = viewer.current_image_source()
            self._skip_next_preview_frame = current_source == asset_path
            if not self._skip_next_preview_frame:
                viewer.reset_zoom()

        # UI State
        self._compare_active = False
        self._set_mode("adjust")
        self._header_controller.switch_to_edit_mode()
        self._zoom_handler.connect_controls()

        if self._theme_controller:
            self._theme_controller.apply_edit_theme()

        # Switch View
        self._router.show_edit()
        self._transition_manager.enter_edit_mode(animate=True)
        _LOGGER.debug(
            "[trace][edit] enter_edit_mode:after_transition_start %s",
            {
                "is_video": self._is_video_source(),
                "surface": self._ui.video_area._diag_surface_name(),
                "adjusted_preview_enabled": self._ui.video_area.adjusted_preview_enabled(),
            },
        )

        # Start Loading High-Res Image / Video
        if not self._is_video_source():
            self._start_async_edit_load(asset_path)

    def _start_async_edit_load(self, source: Path):
        if self._session is None: return
        self._is_loading_edit_image = True
        self._ui.edit_image_viewer.set_loading(not self._skip_next_preview_frame)
        self._pipeline_loader.load_image(source)

    def _start_video_edit_load(self, source: Path) -> None:
        """Initialise video playback and trim UI for the active edit session."""

        if self._session is None:
            return
        self._emit_video_trim_diag("start_video_edit_load", source=source)
        # Probe once and extract both fields to avoid duplicate ffprobe calls.
        video_meta = self._read_video_meta_safe(source)
        self._record_video_rotation(source)
        self._video_frame_step_ms = self._frame_step_ms_from_meta(video_meta)
        self._video_color_stats = None
        self._pending_video_duration_sec = self._duration_sec_from_meta(video_meta)
        self._video_trim_thumbnail_timer.stop()
        self._video_sidebar_preview_timer.stop()
        self._ui.video_area.set_edit_mode_active(True)
        self._ui.video_area.set_controls_enabled(False)
        self._ui.video_area.hide_controls(animate=False)
        self._ui.video_area.set_adjusted_preview_enabled(True)
        self._ui.video_area.set_adjustments(self._resolve_session_adjustments())
        trim_in, trim_out = normalise_video_trim(
            self._session.values(),
            self._pending_video_duration_sec,
        )
        if self._pending_video_duration_sec is not None:
            canonical = self._canonical_trim_updates(
                trim_in,
                trim_out,
                self._pending_video_duration_sec,
            )
            self._session.set_values(canonical, emit_individual=False)
        self._ui.video_area.load_video(
            source,
            adjustments=self._resolve_session_adjustments(),
            trim_range_ms=(int(round(trim_in * 1000.0)), int(round(trim_out * 1000.0))),
            adjusted_preview=True,
        )
        self._ui.video_trim_bar.clear()
        if self._pending_video_duration_sec is not None:
            in_ratio, out_ratio = self._trim_ratios_for_duration(self._pending_video_duration_sec)
            self._ui.video_trim_bar.set_trim_ratios(in_ratio, out_ratio)
            self._ui.video_trim_bar.set_playhead_ratio(in_ratio)
        else:
            self._ui.video_trim_bar.set_trim_ratios(0.0, 1.0)
            self._ui.video_trim_bar.set_playhead_ratio(0.0)
        self._ui.video_trim_bar.set_playing(False)
        self._queue_video_trim_thumbnails(self._pending_video_duration_sec)
        self._ui.video_area.play()

    def _on_edit_image_loaded(self, path: Path, image: QImage):
        if self._session is None or self._current_source != path: return

        try:
            self._preview_manager.start_session(image, self._session.values())
        except Exception:
            _LOGGER.exception("Failed to init preview")
            self.leave_edit_mode()
            return

        resolved = self._resolve_session_adjustments()
        self._active_adjustments = resolved
        self._ui.edit_image_viewer.set_image(image, resolved, image_source=path, reset_view=False)
        self._skip_next_preview_frame = False
        self._is_loading_edit_image = False
        self._ui.edit_image_viewer.set_loading(False)

        # Calculate target height for sidebar previews
        target_height = self._ui.edit_sidebar.preview_thumbnail_height()
        if target_height <= 0:
            target_height = 64  # Fallback

        self._pipeline_loader.prepare_sidebar_preview(
            image,
            target_height=target_height,
            full_res_image_for_fallback=image
        )

    def _on_edit_image_load_failed(self, path: Path, msg: str):
        _LOGGER.error(f"Failed to load edit image: {msg}")
        self.leave_edit_mode()

    def _handle_sidebar_preview_ready(self, result):
        if self._session:
            self._ui.edit_sidebar.set_light_preview_image(result.image, color_stats=result.stats)
            self._ui.edit_sidebar.refresh()

    def leave_edit_mode(self, *, restore_reason: str = "edit_exit"):
        """Returns to detail view."""
        source = self._current_source
        is_video_source = (
            source is not None
            and source.suffix.lower() in VIDEO_EXTENSIONS
        )
        should_restore_detail = (
            source is not None
            and self._media_session is not None
        )
        _LOGGER.debug(
            "[trace][edit] leave_edit_mode:start %s",
            {
                "source": str(source) if source is not None else None,
                "session_exists": self._session is not None,
                "is_video": is_video_source,
                "surface_before": self._ui.video_area._diag_surface_name(),
                "adjusted_preview_before": self._ui.video_area.adjusted_preview_enabled(),
            },
        )
        if self._fullscreen_manager.is_in_fullscreen():
            adjustments = None
            if self._session is not None:
                adjustments = self._resolve_session_adjustments()
            self._fullscreen_manager.exit_fullscreen_preview(source, adjustments)
        if self._session is not None:
            self._active_edit_viewport().setCropMode(False, self._session.values())
        self._active_edit_viewport().set_eyedropper_mode(False)
        self._current_source = None
        self._session = None
        self._preview_manager.stop_session()
        self._zoom_handler.disconnect_controls()
        self._header_controller.restore_detail_mode()
        self._video_color_stats = None
        pending_duration_sec = self._pending_video_duration_sec
        self._pending_video_duration_sec = None
        self._video_trim_thumbnail_timer.stop()
        self._video_sidebar_preview_timer.stop()
        self._video_thumbnail_generation += 1
        self._video_sidebar_generation += 1
        self._video_trim_worker = None
        self._video_sidebar_worker = None
        self._video_trim_diag.clear()
        self._video_frame_step_ms = _DEFAULT_VIDEO_FRAME_STEP_MS

        if self._theme_controller:
            self._theme_controller.restore_global_theme()

        self._ui.edit_image_viewer.set_surface_color_override(
            viewer_surface_color(self._ui.edit_image_viewer)
        )
        self._ui.video_area.set_edit_mode_active(False)
        self._ui.video_area.set_controls_enabled(True)
        self._ui.video_trim_bar.hide()

        self._ui.edit_sidebar.set_session(None)
        self._ui.edit_sidebar.set_video_edit_mode(False)
        self._router.show_detail()
        show_filmstrip = True
        toggle_filmstrip_action = getattr(self._ui, "toggle_filmstrip_action", None)
        if toggle_filmstrip_action is not None:
            show_filmstrip = bool(toggle_filmstrip_action.isChecked())
        self._transition_manager.leave_edit_mode(
            animate=True,
            show_filmstrip=show_filmstrip,
        )
        if should_restore_detail:
            self._media_session.request_restore(
                MediaRestoreRequest(
                    path=source,
                    reason=restore_reason,
                    duration_sec=pending_duration_sec if is_video_source else None,
                )
            )
        _LOGGER.debug(
            "[trace][edit] leave_edit_mode:after_transition_start %s",
            {
                "source": str(source) if source is not None else None,
                "surface_now": self._ui.video_area._diag_surface_name(),
                "adjusted_preview_now": self._ui.video_area.adjusted_preview_enabled(),
            },
        )

    # --- Actions ---

    def _handle_done_clicked(self):
        if not self._session or not self._current_source:
            self.leave_edit_mode()
            return

        source = self._current_source
        self._session.set_values(
            self._active_edit_viewport().crop_values(),
            emit_individual=False,
        )
        if self._adjustment_committer is None:
            navigation = self._navigation
            if navigation:
                navigation.pause_library_watcher()
            try:
                edit_service = self._edit_service()
                if edit_service is None:
                    raise RuntimeError("Edit service is unavailable")
                edit_service.write_adjustments(source, self._session.values())
                self._asset_vm.invalidate_thumbnail(str(source))
            finally:
                if navigation:
                    navigation.resume_library_watcher()
            self.leave_edit_mode(restore_reason="edit_done")
            return
        if self._adjustment_committer.commit(source, self._session.values(), reason="edit_done"):
            self.leave_edit_mode(restore_reason="edit_done")

    def _handle_reset_clicked(self):
        if self._session:
            self.push_undo_state()
            self._session.reset()

    def _handle_rotate_left_clicked(self):
        if self._session:
            self.push_undo_state()
            updates = self._active_edit_viewport().rotate_image_ccw()
            self._session.set_values(updates, emit_individual=False)

    def _handle_compare_pressed(self):
        self._compare_active = True
        viewport = self._active_edit_viewport()
        viewport.set_adjustments({})
        if self._is_video_source():
            duration_ms = self._ui.video_area.player_bar.duration()
            if duration_ms > 0:
                self._ui.video_area.set_trim_range_ms(0, duration_ms)
                self._ui.video_trim_bar.set_trim_ratios(0.0, 1.0)
                self._ui.video_trim_bar.set_playhead_ratio(self._safe_video_ratio(0))

    def _handle_compare_released(self):
        self._compare_active = False
        self._apply_session_adjustments_to_viewer()

    def push_undo_state(self):
        self._history_manager.push_undo_state()

    def undo(self):
        self._history_manager.undo()

    def redo(self):
        self._history_manager.redo()

    # --- Helpers ---

    def _edit_service(self) -> EditServicePort | None:
        if self._edit_service_getter is None:
            return None
        return self._edit_service_getter()

    def _handle_session_changed(self, values: dict):
        """Buffer session updates to avoid spamming the preview pipeline."""
        self._pending_session_values = values
        if not self._update_throttler.isActive():
            self._perform_deferred_update()
            self._update_throttler.start()

    def _perform_deferred_update(self):
        if self._session:
            # Always fetch the authoritative state from the session
            current_values = self._session.values()
            if not self._preview_updates_suspended and not self._is_video_source():
                self._preview_manager.update_adjustments(current_values)
            self._apply_session_adjustments_to_viewer()
            self._pending_session_values = None

    def _apply_session_adjustments_to_viewer(self):
        if self._session and not self._compare_active:
            adj = self._resolve_session_adjustments()
            self._active_adjustments = adj
            self._active_edit_viewport().set_adjustments(adj)
            if self._is_video_source():
                self._apply_video_trim_from_session()

    def _resolve_session_adjustments(self):
        if not self._session:
            return {}
        return self._resolve_adjustments_for_values(self._session.values())

    def _resolve_adjustments_for_values(self, values: dict):
        if self._is_video_source():
            return resolve_adjustment_mapping(values, stats=self._video_color_stats)
        try:
            return self._preview_manager.resolve_adjustments(values)
        except AttributeError:
            return resolve_adjustment_mapping(values, stats=self._preview_manager.color_stats())



    def _handle_mode_change(self, mode: str, checked: bool):
        if checked: self._set_mode(mode)

    def _handle_top_bar_index_changed(self, index: int):
        self._set_mode("adjust" if index == 0 else "crop")

    def _set_mode(self, mode: str):
        new_state = self._adjust_mode if mode == "adjust" else self._crop_mode
        if self._current_mode == new_state: return
        self._current_mode.exit()
        self._current_mode = new_state
        self._current_mode.enter()

    def _handle_sidebar_interaction_started(self) -> None:
        """Suspend heavy preview rendering while the user drags adjustment sliders."""

        self._interaction_depth += 1
        if self._interaction_depth == 1:
            self._preview_updates_suspended = True
            self._preview_manager.cancel_pending_updates()

    def _handle_sidebar_interaction_finished(self) -> None:
        """Re-enable preview rendering after slider interaction completes."""

        if self._interaction_depth > 0:
            self._interaction_depth -= 1
        if self._interaction_depth == 0:
            self._preview_updates_suspended = False
            if self._session is not None:
                self._preview_manager.update_adjustments(self._session.values())

    def shutdown(self):
        """Cleanup resources on app exit."""
        if self._session:
            self.leave_edit_mode()
