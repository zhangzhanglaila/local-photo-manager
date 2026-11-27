"""Trail geometry and painting for :class:`PhotoMapView`.

The trail is drawn through a painter hook registered on the map runtime; the
view only owns the data and the tooltip hit test.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter


class PhotoMapTrailMixin:
    """Render and query the animated location trail over the map."""

    def _trail_tooltip_at(self, screen_pos) -> str | None:
        """Return a tooltip string if the mouse is near a trail segment."""
        try:
            if not self._trail_layer_visible:
                return None
            trail = getattr(self._trail_layer, '_trail_data', None)
            if trail is None:
                return None
            project_fn = getattr(self._map_widget, "project_lonlat", None)
            if project_fn is None:
                return None
            idx = self._trail_layer.segment_at_point(
                QPointF(screen_pos), project_fn, threshold=24.0,
            )
            if idx is None or idx >= len(trail.segments):
                return None
            seg = trail.segments[idx]
            if not seg.points:
                return None
            start_pt = seg.points[0]
            end_pt = seg.points[-1]
            start_time = start_pt.timestamp.strftime("%Y-%m-%dT%H:%M")
            end_time = end_pt.timestamp.strftime("%Y-%m-%dT%H:%M")
            start_loc = start_pt.location_name or f"({start_pt.latitude:.2f},{start_pt.longitude:.2f})"
            end_loc = end_pt.location_name or f"({end_pt.latitude:.2f},{end_pt.longitude:.2f})"
            return f"{start_time} -> {end_time}\n{start_loc} -> {end_loc}"
        except Exception:
            return None



    def _on_view_changed_repaint_trail(self, *_args) -> None:
        """Repaint the overlay when the map view changes and trail is visible.

        For the QWidget-fallback path the overlay does not automatically
        repaint when the map pans or zooms.  The GL post-render path
        handles this implicitly because the trail callback runs inside the
        map's own paint pass.
        """
        if not self._trail_layer_visible:
            return
        if self._overlay_attachment.uses_post_render:
            return  # GL path — no extra work needed
        if hasattr(self._overlay, '_buffer_dirty'):
            self._overlay._buffer_dirty = True
        self._overlay.update()



    def _make_trail_paint_callback(self) -> Callable[[QPainter], None]:
        """Return a closure that paints the trail layer onto *painter*.

        The closure captures ``self._map_widget`` (which must already exist)
        so that ``project_lonlat`` can convert geographic coordinates to the
        widget-local coordinate space used by the painter.
        """
        map_widget = self._map_widget
        trail_layer = self._trail_layer

        def _paint_trail(painter: QPainter) -> None:
            if not self._trail_layer_visible:
                return
            viewport = QRectF(map_widget.rect())
            trail_layer.paint(painter, map_widget.project_lonlat, viewport)

        return _paint_trail



    def _ensure_trail_registered(self) -> None:
        """Register the trail paint callback with the map widget if needed.

        For the GL post-render path the callback is added as a separate
        painter.  For the QWidget fallback path it is wired through the
        ``_MarkerLayer._extra_paint_fn`` hook set during
        ``_build_map_widget``.
        """
        if self._trail_registered:
            return
        if not self._map_widget_built:
            return
        if self._overlay_attachment.supports_post_render(self._map_widget):
            add_painter = getattr(self._map_widget, "add_post_render_painter", None)
            if callable(add_painter):
                add_painter(self._trail_paint_callback)
        self._trail_registered = True



    def _ensure_trail_unregistered(self) -> None:
        """Remove the trail paint callback from the map widget."""
        if not self._trail_registered:
            return
        if self._map_widget_built and self._overlay_attachment.supports_post_render(self._map_widget):
            remove_painter = getattr(self._map_widget, "remove_post_render_painter", None)
            if callable(remove_painter):
                remove_painter(self._trail_paint_callback)
        self._trail_registered = False



    def set_trail(self, trail_data) -> None:
        """Set trail data and show the timeline slider."""
        from ..models.trail_models import TrailData
        if not isinstance(trail_data, TrailData):
            return
        self._trail_layer.set_trail(trail_data)
        self._trail_layer_visible = True
        self._ensure_trail_registered()
        if trail_data.is_empty:
            self._timeline_slider.hide()
        else:
            self._timeline_slider.show()
            if trail_data.start_date and trail_data.end_date:
                self._timeline_slider.set_date_range(trail_data.start_date, trail_data.end_date)
        if self._map_widget_built:
            self.request_full_update()
            # Invalidate the QWidget overlay buffer so the trail is redrawn.
            if hasattr(self._overlay, '_buffer_dirty'):
                self._overlay._buffer_dirty = True



    def clear_trail(self) -> None:
        """Clear trail data, hide the timeline slider, and restore all markers."""
        self._trail_layer.clear()
        self._trail_layer_visible = False
        self._ensure_trail_unregistered()
        self._timeline_slider.hide()
        # Restore all markers (they may have been filtered by timeline range).
        if self._map_widget_built and self._assets_library_root is not None:
            if self._active_person_id and self._person_filter:
                self._person_filter.set_all_geotagged(self._assets)
                filtered = self._person_filter.filter_by_person(self._active_person_id)
                self._marker_controller.set_assets(filtered, self._assets_library_root)
            else:
                self._marker_controller.set_assets(self._assets, self._assets_library_root)
        if self._map_widget_built:
            self.request_full_update()
            if hasattr(self._overlay, '_buffer_dirty'):
                self._overlay._buffer_dirty = True



    def _rebuild_trail(self) -> None:
        """Rebuild trail from current data source, respecting active filters.

        Uses the geotagged set stored by the coordinator (``_trail_geotagged``)
        as the canonical base.  When a person filter is active the trail is
        rebuilt from only that person's geotagged assets — without touching
        the ``_person_filter._all_geotagged`` reference used by markers.
        """
        if not self._trail_layer_visible:
            return
        trail_service = getattr(self, "_trail_service", None)
        if trail_service is None:
            return

        base = getattr(self, "_trail_geotagged", [])
        if not base:
            return

        if self._active_person_id and self._person_filter is not None:
            person_ids = self._person_filter.get_person_asset_ids(self._active_person_id)
            data = [a for a in base if getattr(a, "asset_id", None) in person_ids]
        else:
            data = list(base)

        start = getattr(self._timeline_slider, "current_start", None)
        end = getattr(self._timeline_slider, "current_end", None)
        granularity = getattr(self._timeline_slider, "granularity", "day")

        trail = trail_service.build_trail(
            data, date_from=start, date_to=end, granularity=granularity,
        )
        self._trail_layer.set_trail(trail)

