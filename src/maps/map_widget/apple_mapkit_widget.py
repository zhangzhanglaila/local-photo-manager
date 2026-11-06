"""Apple Maps widget backed by MapKit JS when Qt WebEngine is available."""

from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, QPointF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from maps.map_sources import MapBackendMetadata, MapSourceSpec

from .drag_cursor import DragCursorManager
from .map_renderer import CityAnnotation
from .geometry import MERCATOR_LAT_BOUND, lonlat_to_normalized, normalized_to_lonlat

try:  # pragma: no cover - availability depends on the user's PySide6 package.
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # noqa: BLE001 - handled as a graceful runtime fallback.
    QWebChannel = None  # type: ignore[assignment]
    QWebEngineView = None  # type: ignore[assignment]


MAPKIT_TOKEN_ENV = "IPHOTO_MAPKIT_JS_TOKEN"
TILE_SIZE = 256



def normalized_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Convert normalised Web Mercator coordinates back to lon/lat."""

    x = float(x) % 1.0
    y = min(max(float(y), 0.0), 1.0)
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y))))
    return lon, lat


class _AppleMapKitBridge(QObject):
    """Receive camera updates from the MapKit JS page."""

    def __init__(self, owner: "AppleMapKitWidget") -> None:
        super().__init__(owner)
        self._owner = owner

    @Slot(float, float, float)
    def cameraChanged(self, lon: float, lat: float, zoom: float) -> None:
        self._owner._handle_js_camera_changed(lon, lat, zoom)


class AppleMapKitWidget(QWidget):
    """Render an online Apple Maps background through MapKit JS."""

    viewChanged = Signal(float, float, float)
    panned = Signal(QPointF)
    panFinished = Signal()

    BACKEND_METADATA = MapBackendMetadata(
        min_zoom=2.0,
        max_zoom=19.0,
        provides_place_labels=True,
        tile_kind="raster",
        tile_scheme="xyz",
        fetch_max_zoom=19,
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_source: MapSourceSpec | None = None,
        tile_root: Path | str = "tiles",
        style_path: Path | str = "style.json",
    ) -> None:
        super().__init__(parent)
        del map_source, tile_root, style_path
        self.setObjectName("AppleMapKitWidget")
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._zoom = float(self.BACKEND_METADATA.min_zoom)
        self._min_zoom = float(self.BACKEND_METADATA.min_zoom)
        self._max_zoom = float(self.BACKEND_METADATA.max_zoom)
        self._default_zoom = float(self.BACKEND_METADATA.min_zoom)
        self._center_x = 0.5
        self._center_y = 0.5
        self._web_ready = False
        self._syncing_from_js = False
        self._dragging = False
        self._last_mouse_pos = QPointF()
        self._drag_cursor = DragCursorManager()
        self._web_view: QWidget | None = None
        self._channel: QObject | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

        token = os.environ.get(MAPKIT_TOKEN_ENV, "").strip()
        if QWebEngineView is None or QWebChannel is None:
            self._show_status(
                "Apple Maps requires PySide6 QtWebEngine. Install a PySide6 build "
                "that includes QtWebEngineWidgets to use this online map."
            )
        elif not token:
            self._show_status(
                f"Apple Maps requires a MapKit JS token. Set {MAPKIT_TOKEN_ENV} "
                "before starting iPhotron, then select Apple Maps again."
            )
        else:
            self._create_web_map(token)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        zoom = max(self._min_zoom, min(self._max_zoom, float(zoom)))
        if abs(zoom - self._zoom) <= 1e-6:
            return
        self._zoom = zoom
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def reset_view(self) -> None:
        self._center_x = 0.5
        self._center_y = 0.5
        self._zoom = self._default_zoom
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        world_size = self._world_size()
        self._center_x -= float(delta_x) / world_size
        self._center_y -= float(delta_y) / world_size
        self._wrap_center()
        self._sync_map_camera()
        self.panned.emit(QPointF(float(delta_x), float(delta_y)))
        self._emit_view_change()

    def center_lonlat(self) -> tuple[float, float]:
        return normalized_to_lonlat(self._center_x, self._center_y)

    def shutdown(self) -> None:
        self._reset_drag_cursor()
        if self._web_view is not None:
            page = getattr(self._web_view, "page", lambda: None)()
            if page is not None:
                page.setWebChannel(None)

    def request_full_update(self) -> None:
        self.update()
        if self._web_view is not None:
            self._web_view.update()

    def map_backend_metadata(self) -> MapBackendMetadata:
        return self.BACKEND_METADATA

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        world_position = lonlat_to_normalized(lon, lat)
        if world_position is None:
            return None

        world_size = self._world_size()
        world_x = world_position[0] * world_size
        world_y = world_position[1] * world_size

        center_px = self._center_x * world_size
        center_py = self._center_y * world_size
        delta_x = world_x - center_px
        if delta_x > world_size / 2.0:
            world_x -= world_size
        elif delta_x < -world_size / 2.0:
            world_x += world_size

        top_left_x = center_px - self.width() / 2.0
        top_left_y = center_py - self.height() / 2.0
        return QPointF(world_x - top_left_x, world_y - top_left_y)

    def center_on(self, lon: float, lat: float) -> None:
        normalized = lonlat_to_normalized(lon, lat)
        if normalized is None:
            return
        self._center_x, self._center_y = normalized
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self.center_on(lon, lat)
        if zoom_delta:
            self.set_zoom(self._zoom + float(zoom_delta))

    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        del cities

    def city_at(self, position: QPointF) -> str | None:
        del position
        return None

    def event_target(self) -> QWidget:
        return self._web_view if isinstance(self._web_view, QWidget) else self

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._web_view is not None:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_pos = event.position()
            self._set_drag_cursor()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._web_view is not None:
            super().mouseMoveEvent(event)
            return
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            current_pos = event.position()
            delta = current_pos - self._last_mouse_pos
            self._last_mouse_pos = current_pos
            if not delta.isNull():
                self.pan_by_pixels(delta.x(), delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._web_view is not None:
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._reset_drag_cursor()
            self.panFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._web_view is not None:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        self.set_zoom(self._zoom * (1.0 + delta / 1200.0))
        event.accept()

    def _show_status(self, message: str) -> None:
        label = QLabel(message, self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(
            "background: #eef2f5; color: #334; font-size: 14px; padding: 24px;"
        )
        self._layout.addWidget(label, 1)

    def _create_web_map(self, token: str) -> None:
        assert QWebEngineView is not None
        assert QWebChannel is not None
        web_view = QWebEngineView(self)
        web_view.setObjectName("AppleMapKitWebView")
        self._web_view = web_view
        self._layout.addWidget(web_view, 1)

        bridge = _AppleMapKitBridge(self)
        channel = QWebChannel(self)
        channel.registerObject("bridge", bridge)
        web_view.page().setWebChannel(channel)
        self._channel = channel

        web_view.loadFinished.connect(self._handle_load_finished)
        web_view.setHtml(self._html(token), QUrl("https://localhost/"))

    def _handle_load_finished(self, ok: bool) -> None:
        self._web_ready = bool(ok)
        if ok:
            self._sync_map_camera()

    def _handle_js_camera_changed(self, lon: float, lat: float, zoom: float) -> None:
        normalized = lonlat_to_normalized(lon, lat)
        if normalized is None:
            return
        self._syncing_from_js = True
        try:
            self._center_x, self._center_y = normalized
            self._zoom = max(self._min_zoom, min(self._max_zoom, float(zoom)))
            self._wrap_center()
            self._emit_view_change()
            self.panFinished.emit()
        finally:
            self._syncing_from_js = False

    def _sync_map_camera(self) -> None:
        if not self._web_ready or self._web_view is None or self._syncing_from_js:
            return
        lon, lat = normalized_to_lonlat(self._center_x, self._center_y)
        script = (
            "window.iphotoSetCamera && window.iphotoSetCamera("
            f"{json.dumps(lat)}, {json.dumps(lon)}, {json.dumps(self._zoom)});"
        )
        page = getattr(self._web_view, "page", lambda: None)()
        if page is not None:
            page.runJavaScript(script)

    def _emit_view_change(self) -> None:
        self.viewChanged.emit(float(self._center_x), float(self._center_y), float(self._zoom))

    def _set_drag_cursor(self) -> None:
        self._drag_cursor.set_cursor(Qt.CursorShape.ClosedHandCursor, (self,))

    def _reset_drag_cursor(self) -> None:
        self._drag_cursor.reset((self,))

    def _world_size(self) -> float:
        return float(TILE_SIZE * (2.0 ** self._zoom))

    def _wrap_center(self) -> None:
        self._center_x %= 1.0
        world_size = self._world_size()
        viewport_height = max(1, self.height())
        half_view_ratio = viewport_height / (2.0 * world_size)
        if half_view_ratio >= 0.5:
            self._center_y = 0.5
            return
        self._center_y = min(max(self._center_y, half_view_ratio), 1.0 - half_view_ratio)

    @staticmethod
    def _html(token: str) -> str:
        token_json = json.dumps(token)
        escaped_env = html.escape(MAPKIT_TOKEN_ENV)
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="initial-scale=1.0, user-scalable=no, width=device-width">
  <style>
    html, body, #map {{
      width: 100%;
      height: 100%;
      margin: 0;
      padding: 0;
      overflow: hidden;
      background: #eef2f5;
    }}
    #error {{
      display: none;
      align-items: center;
      justify-content: center;
      height: 100%;
      padding: 24px;
      box-sizing: border-box;
      color: #334;
      font: 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      text-align: center;
    }}
  </style>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <script src="https://cdn.apple-mapkit.com/mk/5.x.x/mapkit.js"></script>
</head>
<body>
  <div id="map"></div>
  <div id="error">Apple Maps failed to load. Check the network connection and {escaped_env}.</div>
  <script>
    const MAPKIT_TOKEN = {token_json};
    let bridge = null;
    let map = null;
    let suppressCameraEvent = false;

    function spanForZoom(zoom) {{
      return Math.max(0.000001, 360 / Math.pow(2, zoom));
    }}

    function zoomFromRegion(region) {{
      const delta = Math.max(region.span.longitudeDelta || 0.000001, 0.000001);
      return Math.max(2, Math.min(19, Math.log2(360 / delta)));
    }}

    function syncCamera() {{
      if (!bridge || !map || suppressCameraEvent) {{
        return;
      }}
      const center = map.center;
      if (!center || !map.region) {{
        return;
      }}
      bridge.cameraChanged(center.longitude, center.latitude, zoomFromRegion(map.region));
    }}

    function showError() {{
      document.getElementById("map").style.display = "none";
      document.getElementById("error").style.display = "flex";
    }}

    window.iphotoSetCamera = function(latitude, longitude, zoom) {{
      if (!map || !window.mapkit) {{
        return;
      }}
      const center = new mapkit.Coordinate(latitude, longitude);
      const span = spanForZoom(zoom);
      const aspect = Math.max(window.innerWidth, 1) / Math.max(window.innerHeight, 1);
      suppressCameraEvent = true;
      map.region = new mapkit.CoordinateRegion(
        center,
        new mapkit.CoordinateSpan(span, Math.max(span * aspect, span))
      );
      window.setTimeout(function() {{
        suppressCameraEvent = false;
      }}, 150);
    }};

    try {{
      new QWebChannel(qt.webChannelTransport, function(channel) {{
        bridge = channel.objects.bridge;
      }});
    }} catch (error) {{
      console.warn("Qt WebChannel is unavailable", error);
    }}

    try {{
      mapkit.init({{
        authorizationCallback: function(done) {{
          done(MAPKIT_TOKEN);
        }}
      }});
      map = new mapkit.Map("map");
      map.showsMapTypeControl = true;
      ["region-change-end", "scroll-end", "zoom-end"].forEach(function(eventName) {{
        try {{
          map.addEventListener(eventName, syncCamera);
        }} catch (error) {{
          console.warn("MapKit event not available", eventName, error);
        }}
      }});
      window.addEventListener("resize", function() {{
        if (map && map.center && map.region) {{
          window.iphotoSetCamera(map.center.latitude, map.center.longitude, zoomFromRegion(map.region));
        }}
      }});
      window.iphotoSetCamera(0, 0, 2);
    }} catch (error) {{
      console.error(error);
      showError();
    }}
  </script>
</body>
</html>"""


__all__ = [
    "AppleMapKitWidget",
    "MAPKIT_TOKEN_ENV",
    "lonlat_to_normalized",
    "normalized_to_lonlat",
]
