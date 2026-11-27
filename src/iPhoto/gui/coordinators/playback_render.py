"""Rendering the current presentation: stills, video, live motion.

The render path of ``PlaybackCoordinator``: react to the view model's
presentation, decide between video and still, defer post-render work, preload the
neighbours and hand live photos to the motion player. Bodies moved verbatim.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer, Slot

from ...i18n import tr
from iPhoto.gui.detail_profile import log_detail_profile

LOGGER = logging.getLogger(__name__)


class PlaybackRenderMixin:
    """Render the detail presentation onto the player surface."""

    def _handle_presentation_changed(self, presentation: DetailPresentation) -> None:
        try:
            self._handle_presentation_changed_impl(presentation)
        except Exception:
            LOGGER.exception("presentation_changed handler failed for row %s", presentation.row)
    def _handle_presentation_changed_impl(self, presentation: DetailPresentation) -> None:
        if (
            self._play_profile_started_at is not None
            and self._play_profile_row == presentation.row
        ):
            elapsed_ms = (time.perf_counter() - self._play_profile_started_at) * 1000.0
            log_detail_profile(
                "playback",
                "presentation_changed",
                elapsed_ms,
                row=presentation.row,
                path=presentation.path.name,
                is_video=presentation.is_video,
            )
        previous = self._current_presentation
        if previous is not None:
            presentation = self._preserve_live_presentation(previous, presentation)
        if not self._router.is_detail_view_active():
            self._clear_play_profile(presentation.row)
            return
        self._current_presentation = presentation
        row = presentation.row
        self._asset_model.set_current_row(row)
        self.assetChanged.emit(row)
        self._update_header(presentation)
        self._sync_filmstrip_selection(row)
        same_asset = (
            previous is not None
            and previous.path == presentation.path
            and previous.reload_token == presentation.reload_token
        )
        if same_asset:
            self._current_presentation = presentation
            self._update_favorite_icon(presentation.is_favorite)
            if self._info_panel and presentation.info_panel_visible:
                self._refresh_info_panel(presentation.info)
                self._info_panel.show()
            elif self._info_panel and self._info_panel.isVisible() and not presentation.info_panel_visible:
                self._info_panel.close()
            self._update_detail_map(presentation.info)
            self._clear_play_profile(presentation.row)
            return
        self._render_presentation(presentation)
    def _preserve_live_presentation(
        self,
        previous: DetailPresentation,
        current: DetailPresentation,
    ) -> DetailPresentation:
        """Keep Live replay metadata stable across same-asset refreshes.

        During rescans the same asset may briefly refresh through a partial row
        that has not yet been re-paired. When the previous Live motion file
        still exists on disk, preserve that replay state for the currently
        displayed asset instead of transiently degrading it to a still image.
        """

        if previous.row != current.row or previous.path != current.path:
            return current
        if current.is_live and current.live_motion_abs is not None:
            return current
        if not previous.is_live or previous.live_motion_abs is None:
            return current
        try:
            if not previous.live_motion_abs.exists():
                return current
        except OSError:
            return current

        info = dict(current.info)
        if previous.live_motion_rel is not None:
            info.setdefault("live_partner_rel", str(previous.live_motion_rel))

        return replace(
            current,
            is_live=True,
            info=info,
            live_motion_rel=previous.live_motion_rel,
            live_motion_abs=previous.live_motion_abs,
        )
    def _render_presentation(self, presentation: DetailPresentation) -> None:
        try:
            self._render_presentation_impl(presentation)
        except Exception:
            LOGGER.exception("render_presentation failed for %s", presentation.path)
    def _render_presentation_impl(self, presentation: DetailPresentation) -> None:
        render_started = time.perf_counter()
        source = presentation.path
        self._active_live_motion = None
        self._active_live_still = None

        self._favorite_button.setEnabled(presentation.can_toggle_favorite)
        self._info_button.setEnabled(True)
        self._share_button.setEnabled(presentation.can_share)
        self._edit_button.setEnabled(presentation.can_edit)
        self._rotate_button.setEnabled(presentation.can_rotate)
        self._update_favorite_icon(presentation.is_favorite)

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(100)
        self._zoom_slider.blockSignals(False)

        if presentation.is_video:
            self._hide_face_name_overlay(clear_annotations=True)
            self._player_view.show_video_surface(interactive=True)
            trim_range_ms = presentation.video_trim_range_ms
            if trim_range_ms is not None:
                self._trim_in_ms, self._trim_out_ms = trim_range_ms
            else:
                self._trim_in_ms = 0
                self._trim_out_ms = 0
            has_trim = trim_range_ms is not None
            load_started = time.perf_counter()
            self._player_view.video_area.load_video(
                source,
                adjustments=presentation.video_adjustments,
                trim_range_ms=trim_range_ms,
                adjusted_preview=presentation.video_adjusted_preview,
            )
            log_detail_profile(
                "playback",
                "video.load_video",
                (time.perf_counter() - load_started) * 1000.0,
                path=source.name,
                adjusted_preview=presentation.video_adjusted_preview,
                has_trim=has_trim,
            )
            self._player_view.video_area.play()
            self._player_bar.setEnabled(True)
            self._zoom_handler.set_viewer(self._player_view.video_area)
            self._player_view.video_area.reset_zoom()
            self._zoom_widget.show()
        else:
            if self._player_view.video_area.has_video():
                self._player_view.video_area.stop()
            self._player_view.show_image_surface()
            # Reset _defer_still_updates left over from a previous Live
            # Photo's _autoplay_live_motion.  Without this, the async
            # image worker for the new photo would see the flag as True
            # and stash the decoded still into _pending_still instead of
            # rendering it — producing a white surface.
            self._player_view.defer_still_updates(False)
            display_started = time.perf_counter()
            self._player_view.display_image(source)
            log_detail_profile(
                "playback",
                "image.display_image",
                (time.perf_counter() - display_started) * 1000.0,
                path=source.name,
            )
            self._player_bar.setEnabled(False)
            self._zoom_handler.set_viewer(self._player_view.image_viewer)
            self._player_view.image_viewer.reset_zoom()
            self._zoom_widget.show()

            if presentation.is_live:
                self._hide_face_name_overlay(clear_annotations=False)
                self._player_view.show_live_badge()
                self._player_view.set_live_replay_enabled(True)
                # Defer _autoplay_live_motion to the next event-loop
                # iteration so that any pending playbackFinished signal
                # from the *previous* Live Photo's motion video is
                # processed first.  At this point _active_live_motion is
                # still None (cleared above), so the stale signal
                # handler returns early instead of triggering a second,
                # conflicting load_video() call that would leave the
                # QMediaPlayer stuck on Windows/WMF.
                QTimer.singleShot(
                    0,
                    lambda p=presentation: self._autoplay_live_motion(p),
                )
            else:
                self._player_view.hide_live_badge()
                self._player_view.set_live_replay_enabled(False)

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

        # Defer expensive metadata/overlay work so the image surface appears
        # without blocking on face-annotation DB queries or info-panel refresh.
        QTimer.singleShot(0, lambda p=presentation: self._deferred_post_render(p))
        log_detail_profile(
            "playback",
            "render_presentation.total",
            (time.perf_counter() - render_started) * 1000.0,
            path=source.name,
            is_video=presentation.is_video,
        )
        self._clear_play_profile(presentation.row)
    def _deferred_post_render(self, presentation: DetailPresentation) -> None:
        """Run expensive metadata/overlay work after the image surface is visible."""

        if not presentation.is_video:
            self._refresh_face_name_overlay_for_presentation(presentation)

        if self._info_panel and presentation.info_panel_visible:
            self._refresh_info_panel(presentation.info)
            self._info_panel.show()
        elif self._info_panel and self._info_panel.isVisible() and not presentation.info_panel_visible:
            self._info_panel.close()
        self._update_detail_map(presentation.info)

        # Preload nearby rows so the next filmstrip click is instant.
        self._detail_vm.preload_nearby(radius=15)

        # Pre-decode next/previous images so arrow-key / filmstrip nav is instant.
        if not presentation.is_video:
            self._preload_adjacent_images(presentation.row)
    def _preload_adjacent_images(self, current_row: int) -> None:
        """Pre-decode nearby images on background threads for smoother navigation.

        Preloads up to 4 images in each direction to ensure instant display when
        navigating with arrow keys or filmstrip clicks.
        """

        # Preload up to 4 images in each direction (total 8 images max)
        # This matches the preload cache size of 8 in player_view_controller.py
        preload_radius = 4
        for offset in range(-preload_radius, preload_radius + 1):
            if offset == 0:
                continue
            row = current_row + offset
            if row < 0:
                continue
            dto = self._detail_vm._store.asset_at(row)
            if dto is None or dto.is_video or dto.abs_path is None:
                continue
            self._player_view.preload_image(dto.abs_path)
    def _autoplay_live_motion(self, presentation: DetailPresentation) -> None:
        motion_path = presentation.live_motion_abs
        if motion_path is None:
            self._refresh_face_name_overlay_for_presentation(presentation)
            return
        self._active_live_motion = motion_path
        self._active_live_still = presentation.path
        self._hide_face_name_overlay(clear_annotations=False)
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        QTimer.singleShot(0, lambda: self._start_live_motion_playback(
            motion_path, presentation.info,
        ))
    def _start_live_motion_playback(
        self, motion_path: Path, info: dict,
    ) -> None:
        """Actually load and play the live-motion video clip."""

        if self._active_live_motion != motion_path:
            return  # user already navigated away
        self._player_view.defer_still_updates(True)
        self._player_view.show_video_surface(interactive=False)
        self._player_view.video_area.load_video(
            motion_path,
            adjustments=None,
            trim_range_ms=None,
            adjusted_preview=False,
        )
        still_time = info.get("still_image_time")
        if isinstance(still_time, (int, float)):
            self._player_view.video_area.seek(int(still_time * 1000))
        self._player_view.video_area.play()
        self._player_bar.setEnabled(False)
        self._is_playing = True
    def _handle_playback_finished(self) -> None:
        if not self._active_live_motion or not self._active_live_still:
            return
        # Loop: replay the live photo motion by seeking to start.
        # Re-calling _autoplay_live_motion() would re-load the entire video
        # (probe + setSource + seek), causing two visible stutters per loop.
        # A simple setPosition(0) keeps the decoder pipeline alive.
        presentation = self._current_presentation
        if presentation is not None and presentation.is_live:
            self._player_view.video_area.seek(0)
            self._player_view.video_area.play()
            return
        still = self._active_live_still
        self._active_live_motion = None
        self._player_view.defer_still_updates(False)
        if not self._player_view.apply_pending_still():
            self._player_view.display_image(still)
        self._player_bar.setEnabled(False)
        self._player_view.show_live_badge()
        self._player_view.set_live_replay_enabled(True)
        self._is_playing = False
        self._refresh_face_name_overlay_for_current_presentation()


__all__ = ["PlaybackRenderMixin"]
