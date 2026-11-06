"""Online no-key map widget backed by public raster tile basemaps."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import (
    QByteArray,
    QObject,
    QPointF,
    QRunnable,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QFont,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkDiskCache,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import QWidget

from maps.map_sources import MapBackendMetadata, MapSourceSpec

from .drag_cursor import DragCursorManager
from .map_renderer import CityAnnotation


TILE_SIZE = 256
MERCATOR_LAT_BOUND = 85.05112878
_MAP_OPAQUE_BACKGROUND = "#d9e4ea"
#: Cap on concurrent tile requests. The host allows only a few parallel
#: connections, so queueing more than this just delays the tiles on screen.
MAX_INFLIGHT_TILES = 12
_ATTRIBUTION_BG = QColor(255, 255, 255, 220)
_ATTRIBUTION_FG = QColor(55, 65, 75)
_DEFAULT_ONLINE_CENTER_LON = 104.1954
_DEFAULT_ONLINE_CENTER_LAT = 35.8617
_DEFAULT_ONLINE_ZOOM = 4.0
_DEFAULT_SINGLE_POINT_ZOOM = 10.0
_NETWORK_FAILURE_FALLBACK_THRESHOLD = 6


@dataclass(frozen=True)
class LeafletTileSource:
    label: str
    url_template: str
    attribution: str
    subdomains: str = "abc"
    max_zoom: int = 19
    retina_url_template: str | None = None


LEAFLET_TILE_SOURCES: dict[str, LeafletTileSource] = {
    "gaode_standard": LeafletTileSource(
        label="Gaode Standard",
        url_template=(
            "https://webrd0{s}.is.autonavi.com/appmaptile?"
            "lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}"
        ),
        attribution="AutoNavi / Gaode",
        subdomains="1234",
        max_zoom=18,
    ),
    "esri_streets": LeafletTileSource(
        label="Esri World Street Map",
        url_template=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Street_Map/MapServer/tile/{z}/{y}/{x}"
        ),
        attribution="Esri World Street Map",
        subdomains="",
        max_zoom=19,
    ),
    "carto_voyager": LeafletTileSource(
        label="CARTO Voyager",
        url_template=(
            "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
        ),
        attribution="OpenStreetMap contributors / CARTO",
        subdomains="abcd",
        max_zoom=20,
    ),
    "osm_standard": LeafletTileSource(
        label="OpenStreetMap",
        url_template="https://tile.openstreetmap.jp/{z}/{x}/{y}.png",
        attribution="OpenStreetMap contributors / OpenStreetMap Japan",
        subdomains="",
        max_zoom=19,
        retina_url_template=(
            "https://tile.openstreetmap.jp/styles/osm-bright/{z}/{x}/{y}@2x.png"
        ),
    ),
}


class _TileDecoder(QObject):
    """Hands decoded tile images back to the GUI thread."""

    decoded = Signal(object, object)  # tile key, QImage | None


class _DecodeTask(QRunnable):
    """Decode one PNG tile off the GUI thread.

    Decoding used to happen inside the network reply handler, so a burst of tile
    arrivals (zoom changes) blocked the UI for tens of milliseconds per frame.
    """

    def __init__(self, key, data: bytes, sink: _TileDecoder) -> None:
        super().__init__()
        self._key = key
        self._data = data
        self._sink = sink

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        image = QImage()
        ok = image.loadFromData(self._data)
        self._sink.decoded.emit(self._key, image if ok else None)


_SHARED_DECODER: _TileDecoder | None = None


def _shared_tile_decoder() -> _TileDecoder:
    global _SHARED_DECODER
    if _SHARED_DECODER is None:
        _SHARED_DECODER = _TileDecoder()
    return _SHARED_DECODER


def lonlat_to_normalized(lon: float, lat: float) -> tuple[float, float] | None:
    """Convert longitude/latitude into normalised Web Mercator coordinates."""

    try:
        lon = float(lon)
        lat = float(lat)
    except (TypeError, ValueError):
        return None

    lat = max(min(lat, MERCATOR_LAT_BOUND), -MERCATOR_LAT_BOUND)
    x = (lon + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)
    return x, y


def normalized_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Convert normalised Web Mercator coordinates back to lon/lat."""

    x = float(x) % 1.0
    y = min(max(float(y), 0.0), 1.0)
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y))))
    return lon, lat


class LeafletOnlineMapWidget(QWidget):
    """Render a no-key online map with Qt-managed raster tile loading."""

    viewChanged = Signal(float, float, float)
    panned = Signal(QPointF)
    panFinished = Signal()
    networkUnavailable = Signal()

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
        del tile_root, style_path
        if not self.objectName():
            self.setObjectName("OnlineRasterMapWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAutoFillBackground(True)
        palette = QPalette(self.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor(_MAP_OPAQUE_BACKGROUND))
        self.setPalette(palette)
        self.setStyleSheet(
            f"QWidget#{self.objectName()} {{ background-color: {_MAP_OPAQUE_BACKGROUND}; border: none; }}"
        )
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        source_kind = map_source.kind if map_source is not None else "carto_voyager"
        self._tile_source = LEAFLET_TILE_SOURCES.get(
            source_kind,
            LEAFLET_TILE_SOURCES["carto_voyager"],
        )
        self._min_zoom = float(self.BACKEND_METADATA.min_zoom)
        self._max_zoom = float(min(self.BACKEND_METADATA.max_zoom, self._tile_source.max_zoom))
        self._default_zoom = max(
            self._min_zoom,
            min(self._max_zoom, _DEFAULT_ONLINE_ZOOM),
        )
        self._zoom = self._default_zoom
        default_center = lonlat_to_normalized(
            _DEFAULT_ONLINE_CENTER_LON,
            _DEFAULT_ONLINE_CENTER_LAT,
        )
        self._default_center_x, self._default_center_y = default_center or (0.5, 0.5)
        self._center_x = self._default_center_x
        self._center_y = self._default_center_y
        self._dragging = False
        self._last_mouse_pos = QPointF()
        self._drag_cursor = DragCursorManager()
        self._tiles: dict[tuple[int, int, int], QPixmap | None] = {}
        self._tile_failure_count = 0
        self._tile_success_count = 0
        self._network_unavailable_emitted = False
        self._tile_device_pixel_ratio = self._preferred_tile_device_pixel_ratio()

        # Requests in flight, keyed by tile. Panning or zooming used to leave the
        # old requests queued on the host's limited connections, so the tiles the
        # user was actually looking at waited behind stale ones and large areas
        # stayed blank. Keeping the handles lets us abort them.
        self._inflight: dict[tuple[int, int, int], QNetworkReply] = {}
        # One coalesced repaint instead of one per arriving tile.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(16)
        self._repaint_timer.timeout.connect(self.update)

        self._decoder = _shared_tile_decoder()
        self._decoder.decoded.connect(self._on_tile_decoded)

        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._handle_tile_reply)
        cache = QNetworkDiskCache(self)
        cache_dir = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
            / "online-map-tiles"
            / source_kind
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.setCacheDirectory(str(cache_dir))
        self._network.setCache(cache)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        zoom = max(self._min_zoom, min(self._max_zoom, float(zoom)))
        if abs(zoom - self._zoom) <= 1e-6:
            return
        self._zoom = zoom
        self._wrap_center()
        self.update()
        self._emit_view_change()

    def reset_view(self) -> None:
        self._center_x = self._default_center_x
        self._center_y = self._default_center_y
        self._zoom = self._default_zoom
        self._wrap_center()
        self.update()
        self._emit_view_change()

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        world_size = self._world_size()
        self._center_x -= float(delta_x) / world_size
        self._center_y -= float(delta_y) / world_size
        self._wrap_center()
        self.update()
        self.panned.emit(QPointF(float(delta_x), float(delta_y)))
        self._emit_view_change()

    def center_lonlat(self) -> tuple[float, float]:
        return normalized_to_lonlat(self._center_x, self._center_y)

    def shutdown(self) -> None:
        self._reset_drag_cursor()

    def request_full_update(self) -> None:
        self.update()

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
        self.update()
        self._emit_view_change()

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self.center_on(lon, lat)
        if zoom_delta:
            self.set_zoom(self._zoom + float(zoom_delta))

    def fit_lonlat_bounds(
        self,
        points: Iterable[tuple[float, float]],
        *,
        padding: int = 96,
        single_point_zoom: float = _DEFAULT_SINGLE_POINT_ZOOM,
        max_zoom: float = 12.0,
    ) -> None:
        normalized_points = [
            normalized
            for lon, lat in points
            if (normalized := lonlat_to_normalized(lon, lat)) is not None
        ]
        if not normalized_points:
            return

        if len(normalized_points) == 1:
            self._center_x, self._center_y = normalized_points[0]
            self._zoom = max(self._min_zoom, min(self._max_zoom, float(single_point_zoom)))
            self._wrap_center()
            self.update()
            self._emit_view_change()
            return

        xs = sorted(point[0] for point in normalized_points)
        ys = [point[1] for point in normalized_points]
        start_x, end_x = self._minimal_wrapped_interval(xs)
        span_x = max(end_x - start_x, 1e-9)
        span_y = max(max(ys) - min(ys), 1e-9)
        center_x = (start_x + span_x / 2.0) % 1.0
        center_y = min(max((min(ys) + max(ys)) / 2.0, 0.0), 1.0)

        available_width = max(1.0, float(self.width() - padding * 2))
        available_height = max(1.0, float(self.height() - padding * 2))
        zoom_x = math.log2(available_width / (TILE_SIZE * span_x))
        zoom_y = math.log2(available_height / (TILE_SIZE * span_y))
        target_zoom = min(float(max_zoom), zoom_x, zoom_y)
        self._center_x = center_x
        self._center_y = center_y
        self._zoom = max(self._min_zoom, min(self._max_zoom, target_zoom))
        self._wrap_center()
        self.update()
        self._emit_view_change()

    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        del cities

    def city_at(self, position: QPointF) -> str | None:
        del position
        return None

    def event_target(self) -> QWidget:
        return self

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._wrap_center()
        self.update()
        self._emit_view_change()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(_MAP_OPAQUE_BACKGROUND))
            self._paint_tiles(painter)
            self._paint_attribution(painter)
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_pos = event.position()
            self._set_drag_cursor()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            current_pos = event.position()
            delta = current_pos - self._last_mouse_pos
            self._last_mouse_pos = current_pos
            if not delta.isNull():
                self.pan_by_pixels(delta.x(), delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._reset_drag_cursor()
            self.panFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        zoom_factor = 1.0 + delta / 1200.0
        new_zoom = max(self._min_zoom, min(self._max_zoom, self._zoom * zoom_factor))
        if abs(new_zoom - self._zoom) <= 1e-6:
            event.accept()
            return

        world_size = self._world_size()
        center_px = self._center_x * world_size
        center_py = self._center_y * world_size
        view_top_left_x = center_px - self.width() / 2.0
        view_top_left_y = center_py - self.height() / 2.0

        mouse_world_x = (view_top_left_x + event.position().x()) / world_size
        mouse_world_y = (view_top_left_y + event.position().y()) / world_size

        self._zoom = new_zoom
        new_world_size = self._world_size()
        self._center_x = (
            mouse_world_x * new_world_size - event.position().x() + self.width() / 2.0
        ) / new_world_size
        self._center_y = (
            mouse_world_y * new_world_size - event.position().y() + self.height() / 2.0
        ) / new_world_size
        self._wrap_center()
        self.update()
        self._emit_view_change()
        event.accept()

    def _paint_tiles(self, painter: QPainter) -> None:
        z = int(max(self._min_zoom, min(self._max_zoom, round(self._zoom))))
        tile_count = 2**z
        world_size_at_z = float(TILE_SIZE * tile_count)
        scale = 2.0 ** (self._zoom - z)
        draw_size = TILE_SIZE * scale
        center_x = self._center_x * world_size_at_z
        center_y = self._center_y * world_size_at_z
        left = center_x - self.width() / (2.0 * scale)
        top = center_y - self.height() / (2.0 * scale)
        right = center_x + self.width() / (2.0 * scale)
        bottom = center_y + self.height() / (2.0 * scale)

        start_x = math.floor(left / TILE_SIZE)
        end_x = math.floor(right / TILE_SIZE)
        start_y = max(0, math.floor(top / TILE_SIZE))
        end_y = min(tile_count - 1, math.floor(bottom / TILE_SIZE))

        visible_keys = {
            (z, raw_x % tile_count, y)
            for y in range(start_y, end_y + 1)
            for raw_x in range(start_x, end_x + 1)
        }
        if self._inflight:
            self._cancel_stale_requests(visible_keys)

        for y in range(start_y, end_y + 1):
            for raw_x in range(start_x, end_x + 1):
                x = raw_x % tile_count
                screen_x = self.width() / 2.0 + (raw_x * TILE_SIZE - center_x) * scale
                screen_y = self.height() / 2.0 + (y * TILE_SIZE - center_y) * scale
                key = (z, x, y)
                pixmap = self._tiles.get(key)
                if pixmap is None:
                    self._request_tile(key)
                    self._paint_tile_placeholder(painter, screen_x, screen_y, draw_size)
                elif not pixmap.isNull():
                    painter.drawPixmap(
                        int(round(screen_x)),
                        int(round(screen_y)),
                        int(math.ceil(draw_size)),
                        int(math.ceil(draw_size)),
                        pixmap,
                    )

    def _paint_tile_placeholder(
        self,
        painter: QPainter,
        x: float,
        y: float,
        size: float,
    ) -> None:
        painter.fillRect(
            int(round(x)),
            int(round(y)),
            int(math.ceil(size)),
            int(math.ceil(size)),
            QColor(_MAP_OPAQUE_BACKGROUND),
        )
        painter.setPen(QPen(QColor(180, 195, 205), 1))
        painter.drawRect(
            int(round(x)),
            int(round(y)),
            int(math.ceil(size)),
            int(math.ceil(size)),
        )

    def _paint_attribution(self, painter: QPainter) -> None:
        text = self._tile_source.attribution
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        rect = metrics.boundingRect(text).adjusted(-6, -3, 6, 3)
        rect.moveBottomRight(self.rect().bottomRight())
        painter.fillRect(rect, _ATTRIBUTION_BG)
        painter.setPen(_ATTRIBUTION_FG)
        painter.drawText(rect.adjusted(3, 1, -3, -1), Qt.AlignmentFlag.AlignCenter, text)

    def _request_tile(self, key: tuple[int, int, int]) -> None:
        if key in self._tiles or key in self._inflight:
            return
        if len(self._inflight) >= MAX_INFLIGHT_TILES:
            # The next repaint retries; queueing more only delays visible tiles.
            return
        z, x, y = key
        self._tiles[key] = None
        url = self._tile_url(z, x, y)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(QByteArray(b"User-Agent"), QByteArray(b"iPhotron/online-map"))
        reply = self._network.get(request)
        reply.setProperty("tile_key", key)
        self._inflight[key] = reply

    def _cancel_stale_requests(self, keep: set[tuple[int, int, int]]) -> None:
        """Abort in-flight tiles that are no longer on screen."""

        for key in [key for key in self._inflight if key not in keep]:
            reply = self._inflight.pop(key, None)
            self._tiles.pop(key, None)
            if reply is not None:
                reply.abort()
                reply.deleteLater()

    def _schedule_repaint(self) -> None:
        """Coalesce tile arrivals into a single repaint per frame."""

        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _handle_tile_reply(self, reply) -> None:
        key = reply.property("tile_key")
        if not isinstance(key, tuple) or len(key) != 3:
            reply.deleteLater()
            return
        self._inflight.pop(key, None)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._tiles.pop(key, None)
            self._record_tile_failure()
            reply.deleteLater()
            self._schedule_repaint()
            return
        data = bytes(reply.readAll())
        reply.deleteLater()
        if data:
            QThreadPool.globalInstance().start(_DecodeTask(key, data, self._decoder))
        else:
            self._tiles.pop(key, None)
            self._record_tile_failure()
            self._schedule_repaint()

    def _on_tile_decoded(self, key, image) -> None:
        """Receive a decoded tile from the worker thread."""

        if image is None:
            self._tiles.pop(key, None)
            self._record_tile_failure()
            self._schedule_repaint()
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._tiles.pop(key, None)
            self._record_tile_failure()
            self._schedule_repaint()
            return
        pixmap.setDevicePixelRatio(self._tile_device_pixel_ratio)
        self._tiles[key] = pixmap
        self._record_tile_success()
        self._schedule_repaint()

    def _record_tile_success(self) -> None:
        self._tile_success_count += 1
        self._tile_failure_count = 0

    def _record_tile_failure(self) -> None:
        self._tile_failure_count += 1
        if self._tile_success_count > 0 or self._network_unavailable_emitted:
            return
        if self._tile_failure_count < _NETWORK_FAILURE_FALLBACK_THRESHOLD:
            return
        self._network_unavailable_emitted = True
        self.networkUnavailable.emit()

    def _tile_url(self, z: int, x: int, y: int) -> str:
        subdomains = self._tile_source.subdomains or "a"
        subdomain = subdomains[(x + y) % len(subdomains)]
        template = (
            self._tile_source.retina_url_template
            if self._tile_device_pixel_ratio > 1.0
            and self._tile_source.retina_url_template is not None
            else self._tile_source.url_template
        )
        return (
            template.replace("{s}", subdomain)
            .replace("{z}", str(z))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
        )

    def _preferred_tile_device_pixel_ratio(self) -> float:
        if self._tile_source.retina_url_template is None:
            return 1.0
        try:
            return 2.0 if self.devicePixelRatioF() >= 1.25 else 1.0
        except Exception:
            return 1.0

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
    def _minimal_wrapped_interval(xs: Sequence[float]) -> tuple[float, float]:
        if len(xs) <= 1:
            value = xs[0] if xs else 0.5
            return value, value

        largest_gap = -1.0
        largest_gap_index = 0
        for index, x in enumerate(xs):
            next_x = xs[(index + 1) % len(xs)]
            gap = (next_x - x) % 1.0
            if gap > largest_gap:
                largest_gap = gap
                largest_gap_index = index

        start = xs[(largest_gap_index + 1) % len(xs)]
        end = xs[largest_gap_index]
        if end < start:
            end += 1.0
        return start, end


__all__ = [
    "LEAFLET_TILE_SOURCES",
    "LeafletOnlineMapWidget",
    "LeafletTileSource",
    "lonlat_to_normalized",
    "normalized_to_lonlat",
]
