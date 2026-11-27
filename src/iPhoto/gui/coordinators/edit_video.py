"""Video trimming and transport for the edit view.

About 480 lines of ``EditCoordinator``: duration/trim maths, the sidebar preview
and its thumbnail requests, play/pause and frame stepping, plus the keyboard
shortcuts that drive them. Bodies moved verbatim from ``edit_coordinator.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, QThreadPool, Qt, QTimer
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

from ...i18n import tr
from iPhoto.core.adjustment_mapping import (
    VIDEO_TRIM_IN_KEY,
    VIDEO_TRIM_OUT_KEY,
    normalise_video_trim,
)
from iPhoto.gui.ui.tasks.video_sidebar_preview_worker import (
    VideoSidebarPreviewResult,
    VideoSidebarPreviewWorker,
)
from iPhoto.gui.ui.tasks.video_trim_thumbnail_worker import VideoTrimThumbnailWorker
from iPhoto.io.metadata import read_video_meta
from iPhoto.utils.ffmpeg import probe_media, video_rotation_from_meta
from ...utils.logging import diag
from .edit_support import (
    _APP_LOGGER,
    _DEFAULT_VIDEO_FRAME_STEP_MS,
    _video_trim_thumbnail_count,
)

_LOGGER = logging.getLogger(__name__)


class EditVideoMixin:
    """Video trim model, sidebar preview and playback transport."""

    def _video_duration_ms(self) -> int:
        return max(int(self._ui.video_area.player_bar.duration()), 0)

    def _video_duration_sec(self) -> float | None:
        # Prefer the probed/player-reported absolute duration tracked by
        # _pending_video_duration_sec.  player_bar.duration() may reflect a
        # trim-relative display value set by PlaybackCoordinator before edit
        # mode was entered, which would cause wrong trim calculations.
        if self._pending_video_duration_sec is not None and self._pending_video_duration_sec > 0.0:
            return self._pending_video_duration_sec
        duration_ms = self._video_duration_ms()
        if duration_ms <= 0:
            return None
        return duration_ms / 1000.0

    def _normalised_video_trim(self) -> tuple[float, float]:
        if self._session is None:
            return (0.0, 0.0)
        return normalise_video_trim(self._session.values(), self._video_duration_sec())

    def _canonical_trim_updates(
        self,
        trim_in_sec: float,
        trim_out_sec: float,
        duration_sec: float | None,
    ) -> dict[str, float]:
        canonical_in = 0.0 if trim_in_sec <= 1e-3 else float(trim_in_sec)
        canonical_out = float(trim_out_sec)
        if duration_sec is not None and canonical_in <= 1e-3 and abs(trim_out_sec - duration_sec) <= 1e-3:
            canonical_out = 0.0
        return {
            VIDEO_TRIM_IN_KEY: canonical_in,
            VIDEO_TRIM_OUT_KEY: canonical_out,
        }

    def _trim_ratios_for_session(self) -> tuple[float, float]:
        return self._trim_ratios_for_duration(self._video_duration_sec())

    def _trim_ratios_for_duration(self, duration_sec: float | None) -> tuple[float, float]:
        if duration_sec is None or duration_sec <= 0.0:
            return (0.0, 1.0)
        trim_in_sec, trim_out_sec = normalise_video_trim(
            self._session.values() if self._session is not None else {},
            duration_sec,
        )
        return (
            max(0.0, min(1.0, trim_in_sec / duration_sec)),
            max(0.0, min(1.0, trim_out_sec / duration_sec)),
        )

    def _safe_video_ratio(self, position_ms: int) -> float:
        duration_ms = self._video_duration_ms()
        if duration_ms <= 0:
            return 0.0
        return max(0.0, min(1.0, float(position_ms) / float(duration_ms)))

    def _apply_video_trim_from_session(self) -> None:
        trim_in_sec, trim_out_sec = self._normalised_video_trim()
        self._ui.video_area.set_trim_range_ms(
            int(round(trim_in_sec * 1000.0)),
            int(round(trim_out_sec * 1000.0)),
        )
        in_ratio, out_ratio = self._trim_ratios_for_session()
        self._ui.video_trim_bar.set_trim_ratios(in_ratio, out_ratio)
        self._ui.video_trim_bar.set_playhead_ratio(
            self._safe_video_ratio(self._ui.video_area.player_bar.position())
        )

    def _refresh_video_sidebar_preview(self) -> None:
        if self._current_source is None or not self._is_video_source():
            return
        duration_sec = self._video_duration_sec()
        trim_in_sec, trim_out_sec = self._normalised_video_trim()
        target_height = self._ui.edit_sidebar.preview_thumbnail_height()
        if target_height <= 0:
            target_height = 64
        self._video_sidebar_generation += 1
        generation = self._video_sidebar_generation
        worker = VideoSidebarPreviewWorker(
            self._current_source,
            generation=generation,
            target_size=QSize(target_height * 3, target_height * 2),
            still_image_time=trim_in_sec + max(trim_out_sec - trim_in_sec, 0.0) * 0.5,
            duration=duration_sec,
            trim_in_sec=trim_in_sec,
            trim_out_sec=trim_out_sec,
        )

        def _handle_ready(result: VideoSidebarPreviewResult, worker_generation: int) -> None:
            if worker_generation != self._video_sidebar_generation:
                return
            if self._session is None or not self._is_video_source():
                return
            self._video_color_stats = result.stats
            self._session.set_color_stats(result.stats)
            self._pipeline_loader.prepare_sidebar_preview_inline(
                result.image,
                target_height=target_height,
                full_res_image_for_fallback=result.image,
            )
            self._apply_session_adjustments_to_viewer()

        def _handle_error(worker_generation: int, message: str) -> None:
            if worker_generation != self._video_sidebar_generation:
                return
            _LOGGER.debug(
                "Failed to generate video sidebar preview for %s: %s",
                self._current_source,
                message,
            )

        def _handle_finished(worker_generation: int) -> None:
            del worker_generation
            try:
                self._video_sidebar_workers.remove(worker)
            except ValueError:
                pass
            if worker is self._video_sidebar_worker:
                self._video_sidebar_worker = None

        self._video_sidebar_worker = worker
        if not hasattr(self, "_video_sidebar_workers"):
            self._video_sidebar_workers = []
        self._video_sidebar_workers.append(worker)
        worker.signals.ready.connect(_handle_ready, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(_handle_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(_handle_finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker, -1)

    def _flush_video_trim_thumbnail_request(self) -> None:
        self._queue_video_trim_thumbnails(self._pending_video_duration_sec)

    def _queue_video_sidebar_preview(self) -> None:
        self._refresh_video_sidebar_preview()

    def _queue_video_trim_thumbnails(self, duration_sec: float | None) -> None:
        if self._current_source is None:
            self._emit_video_trim_diag(
                "skip",
                source=self._current_source,
                duration_sec=duration_sec,
            )
            return
        diag_duration = None if duration_sec is None else round(duration_sec, 3)
        target_width = 96
        width, count = self._estimate_video_trim_thumbnail_request(self._current_source)
        self._video_thumbnail_generation += 1
        generation = self._video_thumbnail_generation
        worker = VideoTrimThumbnailWorker(
            self._current_source,
            generation=generation,
            duration_sec=duration_sec,
            target_height=72,
            target_width=target_width,
            count=count,
        )
        self._video_trim_diag[generation] = {
            "source": str(self._current_source),
            "requested_count": count,
            "duration_sec": diag_duration,
            "received_images": 0,
            "ready_batches": 0,
            "errors": [],
        }
        self._emit_video_trim_diag(
            "queue",
            generation=generation,
            source=self._current_source,
            duration_sec=diag_duration,
            width=width,
            requested_count=count,
        )
        self._ui.video_trim_bar.clear()
        self._video_trim_worker = worker
        worker.signals.thumbnail.connect(
            self._handle_video_trim_thumbnail_image,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.ready.connect(
            self._handle_video_trim_thumbnails_ready,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.error.connect(
            self._handle_video_trim_thumbnail_error,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.finished.connect(
        self._handle_video_trim_thumbnail_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        QThreadPool.globalInstance().start(worker, -1)

    def _handle_video_trim_thumbnail_image(self, image: QImage, generation: int) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_image_stale", generation=generation)
            return
        if self._session is None or not self._is_video_source() or image.isNull():
            self._emit_video_trim_diag(
                "drop_image_invalid",
                generation=generation,
                is_video=self._is_video_source(),
                session=self._session is not None,
                is_null=image.isNull(),
            )
            return
        diag = self._video_trim_diag.setdefault(generation, {})
        received = int(diag.get("received_images", 0)) + 1
        diag["received_images"] = received
        if received <= 3 or received % 5 == 0:
            self._emit_video_trim_diag(
                "image",
                generation=generation,
                received_images=received,
                size=f"{image.width()}x{image.height()}",
            )
        self._apply_video_trim_from_session()
        self._ui.video_trim_bar.add_thumbnail(QPixmap.fromImage(image))

    def _handle_video_trim_thumbnails_ready(self, images: list[QImage], generation: int) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_ready_stale", generation=generation, image_count=len(images))
            return
        if self._session is None or not self._is_video_source():
            self._emit_video_trim_diag(
                "drop_ready_invalid",
                generation=generation,
                image_count=len(images),
                is_video=self._is_video_source(),
                session=self._session is not None,
            )
            return
        diag = self._video_trim_diag.setdefault(generation, {})
        diag["ready_batches"] = int(diag.get("ready_batches", 0)) + 1
        diag["ready_image_count"] = len(images)
        self._emit_video_trim_diag(
            "ready",
            generation=generation,
            image_count=len(images),
            ready_batches=diag["ready_batches"],
        )
        self._apply_video_trim_from_session()
        self._ui.video_trim_bar.set_thumbnails(
            [
                QPixmap.fromImage(image)
                for image in images
                if image is not None and not image.isNull()
            ]
        )

    def _handle_video_trim_thumbnail_error(self, generation: int, message: str) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_error_stale", generation=generation, message=message)
            return
        diag = self._video_trim_diag.setdefault(generation, {})
        errors = list(diag.get("errors", []))
        errors.append(message)
        diag["errors"] = errors
        self._emit_video_trim_diag("error", generation=generation, message=message)
        _LOGGER.warning("Failed to generate trim thumbnails for %s: %s", self._current_source, message)

    def _handle_video_trim_thumbnail_finished(self, generation: int) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_finished_stale", generation=generation)
            return
        diag = self._video_trim_diag.get(generation, {})
        self._emit_video_trim_diag(
            "finished",
            generation=generation,
            requested_count=diag.get("requested_count"),
            received_images=diag.get("received_images"),
            ready_batches=diag.get("ready_batches"),
            ready_image_count=diag.get("ready_image_count"),
            errors=diag.get("errors"),
        )
        self._video_trim_worker = None

    def _estimate_video_trim_thumbnail_request(self, source: Path) -> tuple[int, int]:
        width = self._ui.video_trim_bar.thumbnail_view_width()
        if width < 100:
            width = 1000

        if self._video_rotation_source == source:
            rotation, raw_w, raw_h = self._video_rotation_info
        else:
            # The trim UI can observe a duration signal before the clip finished
            # loading; fall back to a square-ish estimate instead of probing
            # ffprobe from inside a widget signal slot.
            rotation, raw_w, raw_h = (0, 0, 0)
        return width, _video_trim_thumbnail_count(width, rotation, raw_w, raw_h)

    def _handle_video_duration_changed(self, duration_ms: int) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_sec = max(float(duration_ms) / 1000.0, 0.0)
        self._pending_video_duration_sec = duration_sec
        trim_in_sec, trim_out_sec = normalise_video_trim(self._session.values(), duration_sec)
        canonical = self._canonical_trim_updates(trim_in_sec, trim_out_sec, duration_sec)
        self._session.set_values(canonical, emit_individual=False)
        self._apply_video_trim_from_session()
        self._emit_video_trim_diag(
            "duration_changed",
            duration_ms=duration_ms,
            duration_sec=round(duration_sec, 3),
            trim_in=round(trim_in_sec, 3),
            trim_out=round(trim_out_sec, 3),
        )
        active_diag = self._video_trim_diag.get(self._video_thumbnail_generation, {})
        has_thumbnails = bool(active_diag.get("received_images")) or bool(active_diag.get("ready_image_count"))
        desired_count = 0
        if self._current_source is not None:
            _, desired_count = self._estimate_video_trim_thumbnail_request(self._current_source)
        requested_count = int(active_diag.get("requested_count", 0) or 0)
        needs_more = has_thumbnails and requested_count < desired_count
        if self._video_trim_worker is None and (not has_thumbnails or needs_more):
            self._queue_video_trim_thumbnails(duration_sec)
        else:
            self._emit_video_trim_diag(
                "duration_changed_no_requeue",
                generation=self._video_thumbnail_generation,
                worker_active=self._video_trim_worker is not None,
                has_thumbnails=has_thumbnails,
                requested_count=requested_count,
                desired_count=desired_count,
            )
        self._video_sidebar_preview_timer.start()

    def _emit_video_trim_diag(self, stage: str, **fields: object) -> None:
        parts = [f"{key}={value}" for key, value in fields.items()]
        message = f"[video-trim] {stage}"
        if parts:
            message += " | " + ", ".join(parts)
        _APP_LOGGER.warning(message)

    def _handle_video_position_changed(self, position_ms: int) -> None:
        if self._session is None or not self._is_video_source():
            return
        self._ui.video_trim_bar.set_playhead_ratio(self._safe_video_ratio(position_ms))

    def _handle_video_playback_state_changed(self, is_playing: bool) -> None:
        """Keep the trim-bar transport button aligned with the edit preview state."""

        if self._session is None or not self._is_video_source():
            return
        self._ui.video_trim_bar.set_playing(is_playing)

    def _handle_video_trim_play_pause_requested(self) -> None:
        """Toggle playback from the trim bar's demo-style play button."""

        if self._session is None or not self._is_video_source():
            return
        if self._ui.video_area.is_playing():
            self._ui.video_area.pause()
        else:
            self._ui.video_area.play()

    def _handle_video_play_pause_shortcut(self) -> None:
        """Toggle play/pause via keyboard shortcut while in edit mode."""
        if not self._can_handle_video_edit_transport_shortcut(allow_conflicting_focus=True):
            return
        if self._ui.video_area.is_playing():
            self._ui.video_area.pause()
        else:
            self._ui.video_area.play()
        self._ui.video_area.note_activity()

    def _handle_video_frame_step_shortcut(self, direction: int) -> None:
        if direction == 0:
            return
        if not self._can_handle_video_edit_transport_shortcut(allow_conflicting_focus=False):
            return
        step_ms = max(int(self._video_frame_step_ms), 1)
        position_ms = int(self._ui.video_area.player_bar.position())
        self._ui.video_area.pause()
        self._ui.video_area.seek(position_ms + int(direction) * step_ms)
        self._ui.video_area.note_activity()

    def _can_handle_video_edit_transport_shortcut(self, *, allow_conflicting_focus: bool) -> bool:
        if self._session is None or not self._is_video_source():
            return False
        if not self._router.is_edit_view_active():
            return False
        if allow_conflicting_focus:
            return True
        return not self._focus_blocks_video_transport_shortcuts(QApplication.focusWidget())

    @staticmethod
    def _focus_blocks_video_transport_shortcuts(widget: QWidget | None) -> bool:
        if widget is None:
            return False
        return isinstance(
            widget,
            (
                QAbstractSlider,
                QAbstractSpinBox,
                QComboBox,
                QLineEdit,
                QPlainTextEdit,
                QTextEdit,
            ),
        )

    def _record_video_rotation(self, source: Path) -> None:
        """Remember the clip's rotation/size while its probe payload is warm.

        ``read_video_meta`` already ran ffprobe for *source* a moment earlier, so
        this is a cache hit; keeping the result here is what lets the trim
        thumbnail estimator stay ffprobe-free inside a widget signal slot.
        """

        try:
            payload = probe_media(source)
        except Exception:
            _LOGGER.debug("Failed to read display matrix for %s", source, exc_info=True)
            payload = {}
        rotation, width, height, _ = video_rotation_from_meta(payload)
        self._video_rotation_source = source
        self._video_rotation_info = (rotation, width, height)

    def _probe_video_frame_step_ms(self, source: Path) -> int:
        info = self._read_video_meta_safe(source)
        return self._frame_step_ms_from_meta(info)

    def _probe_video_duration_sec(self, source: Path) -> float | None:
        info = self._read_video_meta_safe(source)
        return self._duration_sec_from_meta(info)

    @staticmethod
    def _read_video_meta_safe(source: Path) -> dict[str, object]:
        """Return video metadata dict (empty on failure)."""

        try:
            return read_video_meta(source)
        except Exception:
            _LOGGER.debug("Failed to probe video meta for %s", source, exc_info=True)
            return {}

    @staticmethod
    def _frame_step_ms_from_meta(info: dict[str, object]) -> int:
        try:
            frame_rate = float(info.get("frame_rate"))
        except (TypeError, ValueError):
            return _DEFAULT_VIDEO_FRAME_STEP_MS
        if frame_rate <= 0.0:
            return _DEFAULT_VIDEO_FRAME_STEP_MS
        return max(int(round(1000.0 / frame_rate)), 1)

    @staticmethod
    def _duration_sec_from_meta(info: dict[str, object]) -> float | None:
        try:
            duration_sec = float(info.get("dur") or info.get("duration") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return None
        if duration_sec <= 0.0:
            return None
        return duration_sec

    def _handle_trim_in_ratio_changed(self, ratio: float) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_sec = self._video_duration_sec()
        if duration_sec is None or duration_sec <= 0.0:
            return
        trim_out_ratio = self._ui.video_trim_bar.trim_ratios()[1]
        trim_in_sec = max(0.0, min(1.0, float(ratio))) * duration_sec
        trim_out_sec = max(trim_in_sec, max(0.0, min(1.0, float(trim_out_ratio))) * duration_sec)
        self._session.set_values(
            self._canonical_trim_updates(trim_in_sec, trim_out_sec, duration_sec),
            emit_individual=False,
        )

    def _handle_trim_out_ratio_changed(self, ratio: float) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_sec = self._video_duration_sec()
        if duration_sec is None or duration_sec <= 0.0:
            return
        trim_in_ratio = self._ui.video_trim_bar.trim_ratios()[0]
        trim_in_sec = max(0.0, min(1.0, float(trim_in_ratio))) * duration_sec
        trim_out_sec = max(trim_in_sec, max(0.0, min(1.0, float(ratio))) * duration_sec)
        self._session.set_values(
            self._canonical_trim_updates(trim_in_sec, trim_out_sec, duration_sec),
            emit_individual=False,
        )

    def _handle_trim_playhead_seeked(self, ratio: float) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_ms = self._video_duration_ms()
        if duration_ms <= 0:
            return
        self._ui.video_area.seek(int(round(max(0.0, min(1.0, float(ratio))) * duration_ms)))


__all__ = ["EditVideoMixin"]
