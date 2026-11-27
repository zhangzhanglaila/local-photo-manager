"""Composite widget that embeds the map preview and renders photo markers."""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from typing import Iterable, Optional, cast

from PySide6.QtCore import QObject, QPointF, Qt, QEvent, Signal, Slot
from ....i18n import tr
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ....application.ports import MapInteractionServicePort, MapRuntimePort
from ....application.services.map_interaction_service import LibraryMapInteractionService
from maps.map_widget.map_renderer import CityAnnotation

from ....application.dtos import GeotaggedAsset
from ..tasks.thumbnail_loader import ThumbnailLoader
from .marker_controller import MarkerController, _MarkerCluster  # noqa: F401 - test surface
from .photo_map_filters import PhotoMapFilterMixin
from .photo_map_markers import _GLMarkerLayer, _MarkerLayer
from .photo_map_sources import (
    _MAP_SOURCE_APPLE,
    _MAP_SOURCE_CARTO,
    _MAP_SOURCE_ESRI,
    _MAP_SOURCE_GAODE,
    _MAP_SOURCE_LOCAL,
    _MAP_SOURCE_OSM,
    _ONLINE_MAP_SOURCE_MODES,
    PhotoMapSourceMixin,
)
from .photo_map_trail import PhotoMapTrailMixin
from .custom_tooltip import FloatingToolTip, ToolTipEventFilter
from .map_widget_support import MapEventSurfaceBridge, MapOverlayAttachment
from .map_widget_factory import (
    MapWidgetBase,
    MapSourceSpec,
    NativeOsmAndWidget,
    _MAPS_PACKAGE_ROOT,
    check_opengl_support,  # noqa: F401 - test surface
    choose_map_widget_backend,  # noqa: F401 - test surface
    create_map_widget,
    format_map_runtime_diagnostics,
    format_map_runtime_summary,
    resolve_map_package_root,
)


logger = getLogger(__name__)
_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "maps"
_MAP_OPAQUE_BACKGROUND = "#88a8c2"
# Share of the most extreme longitudes/latitudes ignored when fitting the map.
_FIT_TRIM_RATIO = 0.02
_FIT_TRIM_MIN_POINTS = 20


def _reemit_without_payload(signal):
    """Return a slot that re-emits *signal* and discards the source arguments.

    ``viewChanged`` carries ``(center_x, center_y, zoom)`` and ``panned`` carries a
    ``QPointF``.  Connecting them straight to a zero-argument signal forwarded those
    arguments and raised ``TypeError: userInteracting() only accepts 0 argument(s)``
    on every pan/zoom.
    """

    def _emit(*_args: object) -> None:
        signal.emit()

    return _emit


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




class PhotoMapView(
    PhotoMapSourceMixin, PhotoMapTrailMixin, PhotoMapFilterMixin, QWidget
):
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

    userInteracting = Signal()
    """Emitted while the user pans or zooms, so background scans can yield."""

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
        """Expose the underlying map widget for integration tests.

        Builds the widget on first access, like :meth:`activate_map` does. The
        lazy build exists so the map never blocks startup; requiring callers to
        remember a separate activation step on top of that only made the accessor
        order-dependent.
        """

        if not self._map_widget_built:
            self._ensure_map_widget()
        if not self._map_widget_built:
            raise RuntimeError("Map widget could not be initialized.")
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._map_widget_built:
            return
        self._sync_marker_overlay()
        self._marker_controller.handle_resize()
        # A resize may be the first moment the map has a usable viewport, which
        # is what the one-shot auto-fit is waiting for.
        self._maybe_fit_online_map_to_assets(self._assets)

    def hideEvent(self, event) -> None:
        """Ensure the custom tooltip is dismissed when the view is hidden."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        super().hideEvent(event)

    def focusOutEvent(self, event) -> None:
        """Clear hover feedback when the map relinquishes focus."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        super().focusOutEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
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

    def closeEvent(self, event) -> None:
        """Ensure background workers shut down before the widget closes."""

        # Qt can deliver closeEvent for an instance whose __init__ aborted early
        # (a failing dependency, a test double), so nothing here may assume the
        # tooltip state exists.
        if getattr(self, "_last_tooltip_text", ""):
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        if getattr(self, "_tooltip", None) is not None:
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
        # Panning/zooming is exactly when the map needs CPU for tiles, so tell the
        # host that the user is interacting with the interface.
        self._map_widget.viewChanged.connect(
            _reemit_without_payload(self.userInteracting)
        )
        self._map_widget.panned.connect(
            _reemit_without_payload(self.userInteracting)
        )
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

    # ------------------------------------------------------------------
    # Trail / Timeline support
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Person filter support
    # ------------------------------------------------------------------

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
