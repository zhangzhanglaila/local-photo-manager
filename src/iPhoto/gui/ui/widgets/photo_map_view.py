"""Composite widget that embeds the map preview and renders photo markers."""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, cast

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QEvent, Signal, Slot
from ....i18n import tr
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from ....application.ports import MapInteractionServicePort, MapRuntimePort
from ....application.services.map_interaction_service import LibraryMapInteractionService
from maps.map_widget.map_renderer import CityAnnotation

from ....library.runtime_controller import GeotaggedAsset
from ..tasks.thumbnail_loader import ThumbnailLoader
from .marker_controller import MarkerController, _MarkerCluster
from .custom_tooltip import FloatingToolTip, ToolTipEventFilter
from .map_widget_support import MapEventSurfaceBridge, MapOverlayAttachment
from .map_widget_factory import (
    MapGLWidget,
    MapGLWindowWidget,
    MapWidget,
    MapWidgetBase,
    MapSourceSpec,
    NativeOsmAndWidget,
    _MAPS_PACKAGE_ROOT,
    check_opengl_support,
    choose_map_widget_backend,
    create_map_widget,
    format_map_runtime_diagnostics,
    format_map_runtime_summary,
    resolve_map_package_root,
)


logger = getLogger(__name__)
_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "maps"
_MAP_OPAQUE_BACKGROUND = "#88a8c2"
_MAP_SOURCE_LOCAL = "local"
_MAP_SOURCE_GAODE = "gaode_standard"
_MAP_SOURCE_ESRI = "esri_streets"
_MAP_SOURCE_CARTO = "carto_voyager"
_MAP_SOURCE_OSM = "osm_standard"
_MAP_SOURCE_APPLE = "apple_mapkit"
_ONLINE_MAP_SOURCE_MODES = {
    _MAP_SOURCE_APPLE,
    _MAP_SOURCE_GAODE,
    _MAP_SOURCE_ESRI,
    _MAP_SOURCE_CARTO,
    _MAP_SOURCE_OSM,
}

# Share of the most extreme longitudes/latitudes ignored when fitting the map.
_FIT_TRIM_RATIO = 0.02
_FIT_TRIM_MIN_POINTS = 20


def _robust_fit_points(
    points: list[tuple[float, float]],
    *,
    trim_ratio: float = _FIT_TRIM_RATIO,
) -> list[tuple[float, float]]:
    """Return corner points of a bounding box that ignores far outliers.

    A handful of photos shot far from the rest (a trip, an imported asset, a
    mis-set camera location) otherwise dominate the min/max bounds. The fit then
    zooms out to the whole region and the library's real photo cluster ends up
    squeezed into a corner, which reads as "no photos on the map".
    """

    if len(points) < _FIT_TRIM_MIN_POINTS:
        return points
    lons = sorted(point[0] for point in points)
    lats = sorted(point[1] for point in points)
    offset = int(len(points) * trim_ratio)
    if offset <= 0:
        return points
    last = len(points) - 1 - offset
    min_lon, max_lon = lons[offset], lons[last]
    min_lat, max_lat = lats[offset], lats[last]
    if min_lon >= max_lon or min_lat >= max_lat:
        return points
    return [(min_lon, min_lat), (max_lon, max_lat)]


def _configure_opaque_map_container(
    widget: QWidget,
    *,
    background: str = _MAP_OPAQUE_BACKGROUND,
) -> None:
    """Give map hosts an opaque fallback while their GL child rebuilds."""

    if not widget.objectName():
        widget.setObjectName(type(widget).__name__)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    widget.setAutoFillBackground(True)
    palette = QPalette(widget.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor(background))
    widget.setPalette(palette)
    widget.setStyleSheet(
        f"QWidget#{widget.objectName()} {{ background-color: {background}; border: none; }}"
    )


class _MarkerLayer(QWidget):
    """Transparent overlay that paints thumbnail clusters with callout arrows."""

    MARKER_SIZE = 72
    THUMBNAIL_NATIVE_SIZE = 192
    THUMBNAIL_DISPLAY_SIZE = 56
    BADGE_DIAMETER = 32
    POINTER_HEIGHT = 10
    POINTER_WIDTH = 18
    CORNER_RADIUS = 12

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # The layer is purely visual, therefore it must not intercept input
        # events which are handled by :class:`PhotoMapView` and the map widget.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._clusters: list[_MarkerCluster] = []
        self._pixmaps: Dict[str, QPixmap] = {}
        self._placeholder = self._create_placeholder()
        # Optional extra paint callback (e.g. trail layer) invoked during
        # paintEvent after the marker clusters have been drawn.
        self._extra_paint_fn: Callable[[QPainter], None] | None = None
        self._badge_font = QFont()
        self._badge_font.setBold(True)
        self._badge_font.setPointSize(9)
        self._badge_pen = QPen(QColor("white"))
        self._badge_pen.setWidth(1)
        self._badge_brush = QColor("#d64541")
        # Double buffer: draw to offscreen pixmap, then blit to screen.
        # Eliminates flicker when clear_pixmaps + set_clusters happen in the
        # same event loop iteration.
        self._back_buffer: QPixmap | None = None
        self._buffer_dirty = True

    @property
    def marker_size(self) -> int:
        """Return the logical footprint of each marker."""

        return self.MARKER_SIZE

    @property
    def thumbnail_size(self) -> int:
        """Return the requested thumbnail edge length."""

        return self.THUMBNAIL_NATIVE_SIZE

    @property
    def thumbnail_display_size(self) -> int:
        """Return the on-screen pixel edge length used for thumbnails."""

        return self.THUMBNAIL_DISPLAY_SIZE

    def set_clusters(self, items: Iterable[_MarkerCluster]) -> None:
        """Replace the rendered clusters and schedule a repaint."""

        self._clusters = list(items)
        self._buffer_dirty = True
        self.update()

    def set_thumbnail(self, rel: str, pixmap: QPixmap) -> None:
        """Cache the pixmap associated with *rel* and refresh the overlay."""

        if pixmap.isNull():
            return
        self._pixmaps[rel] = pixmap
        self._buffer_dirty = True
        self.update()

    def clear_pixmaps(self) -> None:
        """Drop cached pixmaps so outdated thumbnails are not reused."""

        self._pixmaps.clear()
        self._buffer_dirty = True
        self.update()

    def paint_markers(self, painter: QPainter) -> None:
        """Paint all marker clusters into an already active painter."""

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for cluster in self._clusters:
            self._paint_cluster(painter, cluster)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        if self._buffer_dirty or self._back_buffer is None or self._back_buffer.size() != self.size():
            buf = QPixmap(self.size())
            buf.fill(Qt.transparent)
            painter = QPainter(buf)
            self.paint_markers(painter)
            painter.end()
            self._back_buffer = buf
            self._buffer_dirty = False
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._back_buffer)
        # Draw extra layers (e.g. trail) on top of the marker buffer.
        if self._extra_paint_fn is not None:
            self._extra_paint_fn(painter)
        painter.end()

    def _paint_cluster(self, painter: QPainter, cluster: _MarkerCluster) -> None:
        width = float(self.MARKER_SIZE)
        display_edge = float(self.THUMBNAIL_DISPLAY_SIZE)
        # The callout should surround the thumbnail with an equal white border on all sides.
        # Deriving the border from the configured marker size keeps the geometry consistent
        # when designers tweak either constant while ensuring horizontal and vertical padding
        # always match.
        border = (width - display_edge) / 2.0
        body_height = display_edge + 2.0 * border
        height = body_height + float(self.POINTER_HEIGHT)
        x = cluster.screen_pos.x() - width / 2.0
        y = cluster.screen_pos.y() - height
        rect = QRectF(x, y, width, height)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = self._create_callout_path(rect)

        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 80), 2))
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPath(path)
        painter.restore()

        thumbnail = self._pixmaps.get(cluster.representative.library_relative)
        if thumbnail is None:
            thumbnail = self._placeholder
        if not thumbnail.isNull():
            thumb_rect = QRectF(
                rect.left() + border,
                rect.top() + border,
                display_edge,
                display_edge,
            )
            painter.save()
            clip_path = QPainterPath()
            # ``setClipPath`` trims the square pixmap into a rounded rectangle so
            # the map overlay mirrors the visual language used by the filmstrip
            # and the rest of the application.
            clip_path.addRoundedRect(thumb_rect, 8.0, 8.0)
            painter.setClipPath(clip_path, Qt.ClipOperation.ReplaceClip)
            painter.drawPixmap(thumb_rect.toRect(), thumbnail)
            painter.restore()

        count = len(cluster.assets)
        if count > 1:
            badge_rect = QRectF(
                rect.right() - self.BADGE_DIAMETER + 4,
                rect.top() - 4,
                self.BADGE_DIAMETER,
                self.BADGE_DIAMETER,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._badge_brush)
            painter.drawEllipse(badge_rect)
            painter.setPen(self._badge_pen)
            painter.setFont(self._badge_font)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(count))

        cluster.bounding_rect = path.boundingRect()

    def _create_callout_path(self, rect: QRectF) -> QPainterPath:
        """Return a speech-bubble style path anchored at the rectangle centre."""

        path = QPainterPath()
        main_rect = QRectF(
            rect.left(),
            rect.top(),
            rect.width(),
            rect.height() - self.POINTER_HEIGHT,
        )
        path.addRoundedRect(main_rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        pointer_top = main_rect.bottom()
        pointer_center_x = main_rect.center().x()
        pointer_path = QPainterPath()
        pointer_path.moveTo(pointer_center_x, pointer_top + self.POINTER_HEIGHT)
        pointer_path.lineTo(pointer_center_x - self.POINTER_WIDTH / 2.0, pointer_top)
        pointer_path.lineTo(pointer_center_x + self.POINTER_WIDTH / 2.0, pointer_top)
        pointer_path.closeSubpath()

        return path.united(pointer_path)

    def _create_placeholder(self) -> QPixmap:
        display_size = self.THUMBNAIL_DISPLAY_SIZE
        pixmap = QPixmap(display_size, display_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QColor("#cccccc"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, display_size, display_size, 8, 8)
        painter.end()
        return pixmap


class _GLMarkerLayer(_MarkerLayer):
    """Marker painter that renders inside the active GL map pass."""

    def __init__(self, target) -> None:
        super().__init__(None)
        self._target = target

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        del args, kwargs
        self._target.request_full_update()


class PhotoMapView(QWidget):
    """Embed the map widget and manage geotagged photo markers."""

    assetActivated = Signal(str)
    """Signal emitted when the user activates a single asset marker."""

    clusterActivated = Signal(object)
    """Signal emitted when the user clicks a cluster with multiple assets.

    The payload is a list of :class:`GeotaggedAsset` objects representing the
    assets aggregated within the clicked cluster at the current zoom level.
    This enables O(1) gallery opening without additional database lookups.
    """

    showAllRequested = Signal()
    """Signal emitted when focused map mode should return to all map assets."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        map_source: MapSourceSpec | None = None,
        map_runtime: MapRuntimePort | None = None,
        map_interaction_service: MapInteractionServicePort | None = None,
    ) -> None:
        super().__init__(parent)
        _configure_opaque_map_container(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout
        self._requested_map_source = map_source
        self._map_source_mode = (
            map_source.kind
            if map_source is not None and map_source.kind in _ONLINE_MAP_SOURCE_MODES
            else _MAP_SOURCE_LOCAL
        )
        self._map_runtime = map_runtime
        self._map_runtime_capabilities = (
            map_runtime.capabilities() if map_runtime is not None else None
        )
        if (
            map_source is None
            and self._map_runtime_capabilities is not None
            and getattr(self._map_runtime_capabilities, "preferred_backend", None)
            in _ONLINE_MAP_SOURCE_MODES
        ):
            self._map_source_mode = cast(
                str,
                getattr(self._map_runtime_capabilities, "preferred_backend"),
            )

        # Trail/timeline support
        from .trail_layer import TrailLayer
        from .timeline_slider import TimelineSlider
        self._trail_layer = TrailLayer()
        self._trail_paint_callback = None
        self._trail_registered = False
        self._timeline_slider = TimelineSlider()
        self._timeline_slider_signals_connected = False
        self._trail_layer_visible = False

        # Person filter support
        self._person_filter_panel = None
        self._person_filter = None
        self._active_person_id: str | None = None
        self._map_interaction_service = (
            map_interaction_service or LibraryMapInteractionService()
        )
        self._map_package_root = resolve_map_package_root(map_runtime)
        self._map_widget: MapWidgetBase
        self._map_event_target: QWidget | None = None
        self._event_bridge = MapEventSurfaceBridge(self)
        self._overlay_attachment = MapOverlayAttachment()
        self._resolved_map_source: MapSourceSpec | None = None
        self._backend_kind = "unavailable"
        self._marker_paint_callback = None
        self._online_map_has_auto_fitted = False
        self._assets: list[GeotaggedAsset] = []
        self._assets_library_root: Path | None = None

        # ``FloatingToolTip`` replicates ``QToolTip`` using a styled ``QFrame``
        # instead of a custom paint routine.  The standard tooltip inherits the
        # translucent attributes from the frameless main window which causes the
        # popup to render as an opaque black rectangle on several window
        # managers.  Keeping a dedicated instance here ensures the tooltip
        # remains available for as long as the map view exists without fighting
        # Qt's global tooltip machinery.
        self._tooltip = FloatingToolTip()
        app = QApplication.instance()
        if app is not None:
            filter_candidate = app.property("floatingToolTipFilter")
            if isinstance(filter_candidate, ToolTipEventFilter):
                # The global filter already manages tooltips originating from
                # standard widgets.  Ignoring the map-specific tooltip prevents
                # the filter from hiding it prematurely when Qt dispatches
                # housekeeping events (for example ``Leave``) to the floating
                # popup itself.
                filter_candidate.ignore_object(self._tooltip)
        self._last_tooltip_text = ""
        self._pending_click_cluster = None  # _MarkerCluster | None
        self._pending_click_pos = QPointF()
        self._thumbnail_loader = ThumbnailLoader(self)
        self._map_widget_built = False
        self._runtime_diagnostics = ""
        self._source_bar = self._build_source_bar()
        self._layout.addWidget(self._source_bar)
        self._placeholder_label = QLabel(tr("map.loading"), self)
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setStyleSheet("color: #888; font-size: 14px;")
        self._layout.addWidget(self._placeholder_label)

    def _build_source_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("mapSourceBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(
            "QWidget#mapSourceBar { background: #f6f8fa; border: none; }"
            "QLabel { color: #263238; font-size: 12px; }"
            "QComboBox {"
            " min-width: 160px;"
            " padding: 4px 28px 4px 8px;"
            " color: #1f2933;"
            " background: #ffffff;"
            " border: 1px solid #aeb8c2;"
            " border-radius: 4px;"
            "}"
            "QComboBox:hover { border-color: #7d8b99; }"
            "QComboBox::drop-down {"
            " width: 22px;"
            " border-left: 1px solid #c6ced6;"
            " background: #eef2f5;"
            "}"
            "QComboBox QAbstractItemView {"
            " color: #1f2933;"
            " background: #ffffff;"
            " selection-color: #ffffff;"
            " selection-background-color: #2f6fed;"
            " border: 1px solid #aeb8c2;"
            " outline: 0;"
            "}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)
        label = QLabel(tr("map.source_label"), bar)
        row.addWidget(label)
        selector = QComboBox(bar)
        selector.addItem(tr("map.source_local"), _MAP_SOURCE_LOCAL)
        selector.addItem(tr("map.source_gaode"), _MAP_SOURCE_GAODE)
        selector.addItem(tr("map.source_esri"), _MAP_SOURCE_ESRI)
        selector.addItem(tr("map.source_carto"), _MAP_SOURCE_CARTO)
        selector.addItem(tr("map.source_osm"), _MAP_SOURCE_OSM)
        selector.addItem(tr("map.source_apple"), _MAP_SOURCE_APPLE)
        source_index = selector.findData(self._map_source_mode)
        selector.setCurrentIndex(max(0, source_index))
        selector.currentIndexChanged.connect(self._handle_map_source_changed)
        row.addWidget(selector)

        self.btn_person_filter = QToolButton(bar)
        self.btn_person_filter.setText("👤 人物")
        self.btn_person_filter.setCheckable(True)
        self.btn_person_filter.setAutoRaise(True)
        self.btn_person_filter.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_person_filter.setFixedHeight(22)
        self.btn_person_filter.setStyleSheet(
            "QToolButton { font-size: 11px; padding: 1px 4px; border: none; }"
            "QToolButton:checked { background: #d0d0d0; border-radius: 3px; }"
        )
        row.addWidget(self.btn_person_filter)

        self.btn_trail_toggle = QToolButton(bar)
        self.btn_trail_toggle.setText("🗺️ 轨迹")
        self.btn_trail_toggle.setCheckable(True)
        self.btn_trail_toggle.setAutoRaise(True)
        self.btn_trail_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_trail_toggle.setFixedHeight(22)
        self.btn_trail_toggle.setStyleSheet(
            "QToolButton { font-size: 11px; padding: 1px 4px; border: none; }"
            "QToolButton:checked { background: #d0d0d0; border-radius: 3px; }"
        )
        row.addWidget(self.btn_trail_toggle)

        row.addStretch(1)
        show_all = QPushButton("显示所有照片", bar)
        show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        show_all.setVisible(False)
        show_all.setStyleSheet(
            "QPushButton {"
            " padding: 4px 10px;"
            " color: #1f2933;"
            " background: #ffffff;"
            " border: 1px solid #aeb8c2;"
            " border-radius: 4px;"
            " font-size: 12px;"
            "}"
            "QPushButton:hover { background: #eef2f5; border-color: #7d8b99; }"
        )
        show_all.clicked.connect(self.showAllRequested.emit)
        row.addWidget(show_all)
        self._map_source_selector = selector
        self._show_all_button = show_all
        return bar

    def set_focused_asset_mode(self, enabled: bool) -> None:
        """Show or hide controls for a map focused on one detail photo."""

        button = getattr(self, "_show_all_button", None)
        if button is not None:
            button.setVisible(bool(enabled))

    def set_map_interaction_service(
        self,
        map_interaction_service: MapInteractionServicePort | None,
    ) -> None:
        """Bind the session-owned marker interaction rules."""

        self._map_interaction_service = (
            map_interaction_service or LibraryMapInteractionService()
        )

    def _ensure_map_widget(self) -> None:
        """Lazily build the map widget on first show to avoid blocking startup."""

        if self._map_widget_built:
            return
        self._placeholder_label.setText(tr("map.loading"))
        try:
            self._build_map_widget()
            self._map_widget_built = True
            self._placeholder_label.hide()
        except Exception:
            logger.exception("_ensure_map_widget: failed to build map widget")
            self._placeholder_label.setText(tr("map.load_failed"))

    def activate_map(self) -> None:
        """Called by the view router when the user navigates to the map view."""

        self._ensure_map_widget()

    @Slot(list)
    def _on_marker_activated(self, assets: list) -> None:
        """Route raw marker assets through the session interaction surface."""

        activation = self._map_interaction_service.activate_marker_assets(assets)
        if activation.kind == "asset" and activation.asset_relative:
            self.assetActivated.emit(activation.asset_relative)
        elif activation.kind == "cluster":
            self.clusterActivated.emit(list(activation.assets))

    def map_widget(self) -> MapWidgetBase:
        """Expose the underlying map widget for integration tests."""

        if not self._map_widget_built:
            raise RuntimeError("Map widget not yet initialized. Call activate_map() first.")
        return self._map_widget

    def set_map_runtime(self, map_runtime: MapRuntimePort | None) -> None:
        """Bind the session-owned map runtime snapshot for later refreshes."""

        previous_capabilities = self._map_runtime_capabilities
        previous_package_root = self._map_package_root
        self._map_runtime = map_runtime
        self._map_runtime_capabilities = (
            map_runtime.capabilities() if map_runtime is not None else None
        )
        if (
            self._requested_map_source is None
            and self._map_runtime_capabilities is not None
            and getattr(self._map_runtime_capabilities, "preferred_backend", None)
            in _ONLINE_MAP_SOURCE_MODES
        ):
            self._map_source_mode = cast(
                str,
                getattr(self._map_runtime_capabilities, "preferred_backend"),
            )
            source_index = self._map_source_selector.findData(self._map_source_mode)
            if source_index >= 0:
                self._map_source_selector.setCurrentIndex(source_index)
        self._map_package_root = resolve_map_package_root(map_runtime)
        if not self._map_widget_built:
            # Widget not built yet — the stored runtime will be used when
            # activate_map() triggers the first build.  Skip rebuild to avoid
            # blocking the UI thread during startup.
            return
        if (
            self._map_runtime_capabilities != previous_capabilities
            or self._map_package_root != previous_package_root
        ):
            self._rebuild_map_widget()

    def uses_native_osmand_widget(self) -> bool:
        """Return ``True`` when the current backend is the native GL widget."""

        if not self._map_widget_built:
            return False
        return isinstance(self._map_widget, NativeOsmAndWidget)

    def runtime_diagnostics(self) -> str:
        """Return the last emitted runtime diagnostics line."""

        return self._runtime_diagnostics

    def request_full_update(self) -> None:
        """Delegate a full repaint to the underlying map widget."""

        if self._map_widget_built:
            request_full_update = getattr(self._map_widget, "request_full_update", None)
            if callable(request_full_update):
                request_full_update()
            elif isinstance(self._map_widget, QWidget):
                self._map_widget.update()

    def set_assets(self, assets: Iterable[GeotaggedAsset], library_root: Path) -> None:
        """Replace the asset catalogue shown on the map."""

        previous_library_root = self._assets_library_root
        self._assets = list(assets)
        self._assets_library_root = library_root
        if previous_library_root != library_root:
            self._online_map_has_auto_fitted = False
        if self._map_widget_built:
            filtered = list(self._assets)

            # Re-apply person filter if active
            if self._active_person_id and self._person_filter:
                self._person_filter.set_all_geotagged(filtered)
                filtered = self._person_filter.filter_by_person(self._active_person_id)

            # Re-apply timeline date filter if active.  This prevents the
            # filter from being lost when the gallery refreshes (e.g.
            # returning from photo detail view).
            if self._trail_layer_visible:
                start = self._timeline_slider.current_start
                end = self._timeline_slider.current_end
                filtered = self._filter_assets_by_date(start, end, filtered)

            self._maybe_fit_online_map_to_assets(filtered)
            self._marker_controller.set_assets(filtered, library_root)

    def clear(self) -> None:
        """Remove all markers from the map."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        self._assets = []
        self._assets_library_root = None
        self._marker_controller.clear()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if not self._map_widget_built:
            return
        self._sync_marker_overlay()
        self._marker_controller.handle_resize()
        # A resize may be the first moment the map has a usable viewport, which
        # is what the one-shot auto-fit is waiting for.
        self._maybe_fit_online_map_to_assets(self._assets)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        """Ensure the custom tooltip is dismissed when the view is hidden."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        super().hideEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        """Clear hover feedback when the map relinquishes focus."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        super().focusOutEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if not self._map_widget_built:
            return super().eventFilter(watched, event)
        if watched is self._map_widget and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self._sync_marker_overlay()
            if event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._maybe_fit_online_map_to_assets(self._assets)
        if watched is self._map_event_target:
            if event.type() == QEvent.Type.MouseMove:
                mouse_event = cast(QMouseEvent, event)
                # If the user drags after pressing, cancel any pending click.
                if self._pending_click_cluster is not None:
                    delta = mouse_event.position() - self._pending_click_pos
                    if delta.manhattanLength() > 6:
                        self._pending_click_cluster = None
                label = self._map_widget.city_at(mouse_event.position())
                if label:
                    global_pos = self._map_event_target.mapToGlobal(mouse_event.position().toPoint())
                    if label != self._last_tooltip_text:
                        self._tooltip.show_text(global_pos, label)
                        self._last_tooltip_text = label
                    else:
                        self._tooltip.show_text(global_pos, label)
                elif self._trail_layer_visible:
                    # Check if hovering over a trail segment
                    label = self._trail_tooltip_at(mouse_event.position())
                    if label:
                        global_pos = self._map_event_target.mapToGlobal(
                            mouse_event.position().toPoint()
                        )
                        self._tooltip.show_text(global_pos, label)
                        self._last_tooltip_text = label
                    elif self._last_tooltip_text:
                        self._tooltip.hide_tooltip()
                        self._last_tooltip_text = ""
                else:
                    if self._last_tooltip_text:
                        self._tooltip.hide_tooltip()
                        self._last_tooltip_text = ""
            elif event.type() == QEvent.Type.Leave:
                self._pending_click_cluster = None
                if self._last_tooltip_text:
                    self._tooltip.hide_tooltip()
                    self._last_tooltip_text = ""
            elif event.type() == QEvent.Type.MouseButtonPress:
                mouse_event = cast(QMouseEvent, event)
                if self._last_tooltip_text:
                    self._tooltip.hide_tooltip()
                    self._last_tooltip_text = ""
                # Record the press — activate on release if no drag occurs.
                cluster = self._marker_controller.cluster_at(mouse_event.position())
                self._pending_click_cluster = cluster
                self._pending_click_pos = mouse_event.position()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                mouse_event = cast(QMouseEvent, event)
                cluster = self._pending_click_cluster
                self._pending_click_cluster = None
                if cluster is not None:
                    # Verify the mouse is still over the same cluster.
                    current = self._marker_controller.cluster_at(
                        mouse_event.position()
                    )
                    if current is not None and current.representative.asset_id == cluster.representative.asset_id:
                        self._marker_controller.handle_marker_click(cluster)
                        return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Ensure background workers shut down before the widget closes."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        self._tooltip.hide_tooltip()
        self._tooltip.deleteLater()
        self._teardown_map_widget()
        super().closeEvent(event)

    def _handle_city_annotations(self, cities: Iterable[CityAnnotation]) -> None:
        """Forward city annotations to the map widget for background rendering."""

        if not self._map_widget_built:
            return
        self._map_widget.set_city_annotations(list(cities))

    def _handle_clusters_updated(self, clusters: object) -> None:
        """Publish marker clusters and keep the QWidget overlay visible."""

        if not hasattr(self, "_overlay"):
            return
        self._overlay.set_clusters(clusters)
        self._sync_marker_overlay()

    def _sync_marker_overlay(self) -> None:
        if not self._map_widget_built or not hasattr(self, "_overlay"):
            return
        if self._overlay_attachment.uses_post_render:
            self._overlay.update()
            return
        self._overlay_attachment.sync_widget_overlay(
            self._overlay,
            geometry=self._map_widget.geometry(),
            raise_overlay=True,
        )

    def _active_map_source(self) -> MapSourceSpec | None:
        if self._map_source_mode == _MAP_SOURCE_GAODE:
            return MapSourceSpec.gaode_standard()
        if self._map_source_mode == _MAP_SOURCE_ESRI:
            return MapSourceSpec.esri_streets()
        if self._map_source_mode == _MAP_SOURCE_CARTO:
            return MapSourceSpec.carto_voyager()
        if self._map_source_mode == _MAP_SOURCE_OSM:
            return MapSourceSpec.osm_standard()
        if self._map_source_mode == _MAP_SOURCE_APPLE:
            return MapSourceSpec.apple_mapkit()
        return self._requested_map_source

    def _handle_map_source_changed(self, index: int) -> None:
        mode = self._map_source_selector.itemData(index)
        if mode not in {
            _MAP_SOURCE_LOCAL,
            *_ONLINE_MAP_SOURCE_MODES,
        }:
            return
        if mode == self._map_source_mode:
            return
        self._map_source_mode = str(mode)
        self._online_map_has_auto_fitted = False
        if not self._map_widget_built:
            return
        self._rebuild_map_widget()

    def _handle_online_map_unavailable(self) -> None:
        if self._resolved_map_source is None:
            return
        if self._resolved_map_source.kind not in _ONLINE_MAP_SOURCE_MODES:
            return
        logger.info("Online map unavailable; falling back to local map.")
        self._map_source_mode = _MAP_SOURCE_LOCAL
        self._online_map_has_auto_fitted = False
        source_index = self._map_source_selector.findData(_MAP_SOURCE_LOCAL)
        if source_index >= 0:
            previous_blocked = self._map_source_selector.blockSignals(True)
            try:
                self._map_source_selector.setCurrentIndex(source_index)
            finally:
                self._map_source_selector.blockSignals(previous_blocked)
        if self._map_widget_built:
            self._rebuild_map_widget()

    def _maybe_fit_online_map_to_assets(self, assets: Iterable[GeotaggedAsset]) -> None:
        if self._online_map_has_auto_fitted:
            return
        if self._resolved_map_source is None:
            return
        if self._resolved_map_source.kind not in _ONLINE_MAP_SOURCE_MODES:
            return
        fit_bounds = getattr(self._map_widget, "fit_lonlat_bounds", None)
        if not callable(fit_bounds):
            return
        # Fitting against a not-yet-laid-out widget measures a 1x1 viewport and
        # computes a meaningless zoom, so leave the work for the resize that
        # follows instead of burning the one-shot flag here.
        widget = self._map_widget
        if widget is None or widget.width() < 64 or widget.height() < 64:
            return

        points = [
            (asset.longitude, asset.latitude)
            for asset in assets
            if isinstance(asset, GeotaggedAsset)
        ]
        if not points:
            return
        fit_bounds(_robust_fit_points(points))
        self._online_map_has_auto_fitted = True

    def _build_map_widget(self) -> None:
        result = create_map_widget(
            self,
            map_source=self._active_map_source(),
            map_runtime_capabilities=self._map_runtime_capabilities,
            package_root=self._map_package_root,
            log=logger,
            context="photo map",
        )
        if result.widget is None:
            raise RuntimeError("Photo map widget backend unavailable")

        self._map_widget = result.widget
        self._backend_kind = result.backend_kind
        self._resolved_map_source = result.resolved_map_source
        assert self._resolved_map_source is not None
        if self._map_runtime_capabilities is not None:
            logger.debug("Photo map runtime capability: %s", self._map_runtime_capabilities.status_message)
        self._layout.insertWidget(1, self._map_widget, 1)

        # Trail paint callback -- wraps TrailLayer.paint() with the map
        # widget's project_lonlat so it can draw on any QPainter.
        self._trail_paint_callback = self._make_trail_paint_callback()

        if self._overlay_attachment.supports_post_render(self._map_widget):
            self._overlay = _GLMarkerLayer(self._map_widget)
            self._overlay_attachment.attach(
                self._map_widget,
                callback=self._overlay.paint_markers,
            )
        else:
            self._overlay = _MarkerLayer(self)
            self._overlay._extra_paint_fn = self._trail_paint_callback
            self._overlay_attachment.attach(
                self._map_widget,
                callback=None,
                overlay=self._overlay,
                overlay_geometry=self._map_widget.geometry(),
                raise_overlay=True,
            )
        self._marker_paint_callback = self._overlay_attachment.callback

        self._event_bridge.bind(self._map_widget)
        self._map_event_target = cast(QWidget | None, self._event_bridge.event_target())
        network_unavailable = getattr(self._map_widget, "networkUnavailable", None)
        if network_unavailable is not None and hasattr(network_unavailable, "connect"):
            network_unavailable.connect(self._handle_online_map_unavailable)
        self._runtime_diagnostics = format_map_runtime_diagnostics(
            self._map_widget,
            backend_kind=result.backend_kind,
            map_source=self._resolved_map_source,
        )
        logger.info(
            format_map_runtime_summary(
                self._map_widget,
                backend_kind=result.backend_kind,
                map_source=self._resolved_map_source,
            )
        )
        logger.debug(self._runtime_diagnostics)
        self._online_map_has_auto_fitted = False

        self._marker_controller = MarkerController(
            self._map_widget,
            self._thumbnail_loader,
            marker_size=self._overlay.marker_size,
            thumbnail_size=self._overlay.thumbnail_size,
            provides_place_labels=self._map_widget.map_backend_metadata().provides_place_labels,
            parent=self,
        )

        self._map_widget.viewChanged.connect(self._marker_controller.handle_view_changed)
        self._map_widget.panned.connect(self._marker_controller.handle_pan)
        self._map_widget.panFinished.connect(self._marker_controller.handle_pan_finished)
        # When the trail is visible, the QWidget-fallback overlay must
        # repaint on every view change so the trail tracks the new
        # projection.  The GL post-render path handles this implicitly.
        self._map_widget.viewChanged.connect(self._on_view_changed_repaint_trail)
        self._thumbnail_loader.ready.connect(self._marker_controller.handle_thumbnail_ready)
        self._marker_controller.clustersUpdated.connect(self._handle_clusters_updated)
        self._marker_controller.citiesUpdated.connect(self._handle_city_annotations)
        self._marker_controller.markerActivated.connect(self._on_marker_activated)
        self._marker_controller.thumbnailUpdated.connect(self._overlay.set_thumbnail)
        self._marker_controller.thumbnailsInvalidated.connect(self._overlay.clear_pixmaps)
        if self._assets_library_root is not None:
            self._maybe_fit_online_map_to_assets(self._assets)
            self._marker_controller.set_assets(self._assets, self._assets_library_root)

        # Add timeline slider at the bottom (hidden by default)
        if self._timeline_slider.parentWidget() is None:
            self._layout.addWidget(self._timeline_slider)
        self._timeline_slider.hide()
        if not self._timeline_slider_signals_connected:
            self._timeline_slider.rangeChanged.connect(self._on_timeline_range_changed)
            self._timeline_slider.granularityChanged.connect(self._on_timeline_granularity_changed)
            self._timeline_slider_signals_connected = True
        self._map_widget_built = True

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

    # ------------------------------------------------------------------
    # Trail / Timeline support
    # ------------------------------------------------------------------

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

    def _on_timeline_range_changed(self, start, end) -> None:
        self._rebuild_trail()
        # Also filter map markers to only show photos within the selected
        # date range, so the map reflects the timeline selection.
        self._apply_timeline_marker_filter(start, end)
        self.request_full_update()

    def _on_timeline_granularity_changed(self, granularity: str) -> None:
        self._rebuild_trail()
        self._apply_timeline_marker_filter(
            self._timeline_slider.current_start,
            self._timeline_slider.current_end,
        )
        self.request_full_update()

    def _filter_assets_by_date(
        self, start, end, assets: list | None = None
    ) -> list:
        """Return assets whose timestamp falls within [*start*, *end*]."""
        source = assets if assets is not None else self._assets
        result: list = []
        for a in source:
            ts = None
            if a.still_image_time is not None:
                ts = a.still_image_time
            elif a.timestamp is not None:
                ts = a.timestamp
            if ts is not None and start.timestamp() <= ts <= end.timestamp():
                result.append(a)
        return result

    def _apply_timeline_marker_filter(self, start, end) -> None:
        """Update map markers to show only assets in the selected date range."""
        if not self._map_widget_built or not self._assets:
            return
        library_root = self._assets_library_root
        if library_root is None:
            return
        filtered = self._filter_assets_by_date(start, end)
        # Preserve active person filter on top of the date filter.
        if self._active_person_id and self._person_filter:
            self._person_filter.set_all_geotagged(filtered)
            filtered = self._person_filter.filter_by_person(self._active_person_id)
        self._marker_controller.set_assets(filtered, library_root)

    # ------------------------------------------------------------------
    # Person filter support
    # ------------------------------------------------------------------

    def show_person_filter(self, people_service) -> None:
        """Show the person filter panel on the right side of the map splitter."""
        from .person_map_panel import PersonMapPanel
        from iPhoto.application.services.person_map_filter import PersonMapFilter

        if self._person_filter_panel is not None:
            return

        self._person_filter = PersonMapFilter(people_service)
        if self._assets:
            self._person_filter.set_all_geotagged(self._assets)

        self._person_filter_panel = PersonMapPanel(people_service)
        self._person_filter_panel.personSelected.connect(self._on_person_selected)
        self._person_filter_panel.personDeselected.connect(self._on_person_deselected)
        self._person_filter_panel.setMinimumWidth(180)
        self._person_filter_panel.setMaximumWidth(360)

        splitter = self._find_map_splitter()
        if splitter is not None:
            splitter.addWidget(self._person_filter_panel)
            total = splitter.width()
            splitter.setSizes([max(total - 200, 400), 200])

    def hide_person_filter(self) -> None:
        """Hide the person filter panel."""
        if self._person_filter_panel is None:
            return
        self._person_filter_panel.personSelected.disconnect()
        self._person_filter_panel.personDeselected.disconnect()
        self._person_filter_panel.hide()
        self._person_filter_panel.setParent(None)
        self._person_filter_panel.deleteLater()
        self._person_filter_panel = None
        self._person_filter = None
        self._active_person_id = None

    def _find_map_splitter(self):
        """Find the QSplitter that contains this map view."""
        from PySide6.QtWidgets import QApplication, QSplitter
        widget = self
        while widget is not None:
            if isinstance(widget, QSplitter):
                return widget
            widget = widget.parent()
        for tl in QApplication.topLevelWidgets():
            if hasattr(tl, 'ui') and hasattr(tl.ui, '_map_splitter'):
                return tl.ui._map_splitter
        return None

    def _on_person_selected(self, person_id: str) -> None:
        if self._person_filter is None:
            return
        self._active_person_id = person_id
        filtered = self._person_filter.filter_by_person(person_id)
        if self._map_widget_built:
            self._marker_controller.set_assets(filtered, self._assets_library_root)
        self._rebuild_trail()

    def _on_person_deselected(self) -> None:
        self._active_person_id = None
        if self._map_widget_built and self._assets_library_root:
            self._marker_controller.set_assets(self._assets, self._assets_library_root)
        self._rebuild_trail()

    def _teardown_map_widget(self) -> None:
        if not self._map_widget_built:
            return
        self._ensure_trail_unregistered()
        self._event_bridge.unbind()
        self._map_event_target = None
        self._overlay_attachment.detach(self._map_widget)
        self._marker_paint_callback = None
        if hasattr(self, "_marker_controller"):
            # ``MarkerController`` maintains a worker thread that aggregates marker clusters.
            # Explicitly shutting it down prevents the Qt event loop from waiting indefinitely.
            self._marker_controller.shutdown()
            self._marker_controller.deleteLater()
        if hasattr(self, "_overlay"):
            self._overlay.hide()
            self._overlay.deleteLater()
        # The map widget owns a ``TileManager`` that runs in a separate ``QThread`` to
        # stream map tiles.  If the thread is not told to exit, the application process
        # keeps running after the window closes, so we must always shut it down here.
        self._layout.removeWidget(self._map_widget)
        self._map_widget.shutdown()
        self._map_widget.hide()
        self._map_widget.setParent(None)
        self._map_widget.deleteLater()
        self._map_widget_built = False

    def _rebuild_map_widget(self) -> None:
        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        if hasattr(self, "_map_widget"):
            self._teardown_map_widget()
        self._placeholder_label.setText(tr("map.loading"))
        self._placeholder_label.show()
        try:
            self._build_map_widget()
        except Exception:
            logger.exception("_rebuild_map_widget: failed to rebuild map widget")
            self._placeholder_label.setText(tr("map.load_failed"))
            return
        self._placeholder_label.hide()


__all__ = ["PhotoMapView"]
