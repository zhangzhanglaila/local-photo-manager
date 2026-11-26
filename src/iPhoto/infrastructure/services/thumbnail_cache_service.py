from collections import OrderedDict
from pathlib import Path
import shutil
from typing import Dict, Optional, Set

import numpy as np
from PySide6.QtCore import QObject, QSize, Signal, QThreadPool, QRunnable, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform

from iPhoto.application.ports import EditServicePort
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.core import geo_utils
from iPhoto.core.image_filters import apply_adjustments
from iPhoto.io import sidecar
from iPhoto.utils import image_loader

class ThumbnailWorkerSignals(QObject):
    """Signals emitted by thumbnail generation workers."""

    result = Signal(Path, QSize, QImage)


class ThumbnailGenerationTask(QRunnable):
    """Background task to generate a thumbnail."""

    def __init__(
        self,
        renderer,
        path: Path,
        size: QSize,
        signals: ThumbnailWorkerSignals,
    ):
        super().__init__()
        self._renderer = renderer
        self._path = path
        self._size = size
        self._signals = signals

    def run(self):
        try:
            # Generate logic (CPU intensive)
            qimg = self._renderer(self._path, self._size)
            if qimg is not None and not qimg.isNull():
                # Emit result back to main thread
                self._signals.result.emit(self._path, self._size, qimg)
        except Exception:
            # Silently fail or log in generator
            pass


class ThumbnailDiskLoadTask(QRunnable):
    """Background task to load a cached thumbnail from disk."""

    def __init__(
        self,
        disk_file: Path,
        path: Path,
        size: QSize,
        signals: ThumbnailWorkerSignals,
    ):
        super().__init__()
        self._disk_file = disk_file
        self._path = path
        self._size = size
        self._signals = signals

    def run(self):
        # Emit even when decoding failed: the service uses that signal to drop the
        # unreadable cache entry and regenerate instead of leaving the key pending.
        try:
            qimg = QImage(str(self._disk_file))
        except Exception:
            qimg = QImage()
        self._signals.result.emit(self._path, self._size, qimg)

class ThumbnailCacheService(QObject):
    """
    Manages thumbnail caching (Memory + Disk) and asynchronous generation.
    """

    thumbnailReady = Signal(Path)

    def __init__(self, disk_cache_path: Path | None = None, memory_limit_mb: int = 300):
        super().__init__()
        self._disk_cache_path: Path | None = None
        self._generator = PillowThumbnailGenerator()
        self._edit_service: EditServicePort | None = None

        # In-memory cache with size limit
        self._memory_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._max_memory_items = 500  # Reduced from 1000 to save memory

        self._pending_tasks: Set[str] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._is_shutting_down = False

        # Throttling: limit concurrent tasks
        self._max_concurrent_tasks = 10

        self.set_disk_cache_path(disk_cache_path)

    def shutdown(self):
        """Prevents new tasks from being submitted and clears pending logic."""
        self._is_shutting_down = True
        self._pending_tasks.clear()

    def set_disk_cache_path(self, disk_cache_path: Path | None) -> None:
        """Point the disk cache at *disk_cache_path*; ``None`` unbinds it.

        The app constructs this service while booting, before a library exists,
        so an unbound cache must not create a directory anywhere — the old
        ``Path.home()`` fallback left ``~/.iPhoto/cache/thumbs`` behind on every
        launch. Unbound requests to :meth:`get_thumbnail` are no-ops.
        """

        self._is_shutting_down = False
        if disk_cache_path is None:
            self._disk_cache_path = None
            self._memory_cache.clear()
            self._pending_tasks.clear()
            return
        resolved = Path(disk_cache_path)
        if self._disk_cache_path == resolved:
            return
        self._disk_cache_path = resolved
        resolved.mkdir(parents=True, exist_ok=True)
        self._memory_cache.clear()
        self._pending_tasks.clear()

    def set_edit_service(self, edit_service: EditServicePort | None) -> None:
        """Bind the current edit surface used for thumbnail rendering."""

        self._edit_service = edit_service

    def get_thumbnail(self, path: Path, size: QSize) -> Optional[QPixmap]:
        if self._is_shutting_down or self._disk_cache_path is None:
            return None

        key = self._cache_key(path, size)

        # 1. Memory Check
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
            return self._memory_cache[key]

        # 2. Throttle: don't queue too many tasks
        if len(self._pending_tasks) >= self._max_concurrent_tasks:
            return None

        # 3. Disk Check — async load to avoid blocking the main thread
        if key not in self._pending_tasks:
            disk_file = self._disk_cache_path / f"{key}.jpg"
            if disk_file.exists():
                self._pending_tasks.add(key)
                self._start_disk_load(disk_file, path, size)
            else:
                # 4. Trigger Async Generation if not pending
                self._pending_tasks.add(key)
                self._start_generation(path, size)

        # Return None while loading — delegate shows micro_thumbnail placeholder
        return None

    def _start_generation(self, path: Path, size: QSize):
        worker_signals = ThumbnailWorkerSignals()
        worker_signals.result.connect(self._handle_generation_result)
        worker = ThumbnailGenerationTask(self._render_thumbnail, path, size, worker_signals)
        self._thread_pool.start(worker)

    def _start_disk_load(self, disk_file: Path, path: Path, size: QSize):
        worker_signals = ThumbnailWorkerSignals()
        worker_signals.result.connect(self._handle_disk_load_result)
        worker = ThumbnailDiskLoadTask(disk_file, path, size, worker_signals)
        self._thread_pool.start(worker)

    def _handle_generation_result(self, path: Path, size: QSize, image: QImage):
        # Back on main thread
        if not image.isNull():
            key = self._cache_key(path, size)
            pixmap = QPixmap.fromImage(image)

            # Only a freshly rendered thumbnail is written: it is the one that was
            # missing from the cache.
            if self._disk_cache_path is None:
                self._pending_tasks.discard(key)
                return
            disk_file = self._disk_cache_path / f"{key}.jpg"
            pixmap.save(str(disk_file), "JPEG")

            self._add_to_memory(key, pixmap)
            self._pending_tasks.discard(key)

            self.thumbnailReady.emit(path)

    def _handle_disk_load_result(self, path: Path, size: QSize, image: QImage):
        """Adopt a thumbnail that was read from the disk cache.

        Writing it back here used to re-encode and overwrite the file on every
        cache hit: a decode + JPEG encode + disk write on the GUI thread for data
        that was already correct, with generation loss on each pass.
        """

        key = self._cache_key(path, size)
        if image.isNull():
            # Unreadable cache entry (truncated / bad format): drop it and rebuild
            # from the source instead of leaving the key pending forever.
            try:
                if self._disk_cache_path is not None:
                    (self._disk_cache_path / f"{key}.jpg").unlink(missing_ok=True)
            except OSError:
                pass
            self._pending_tasks.discard(key)
            if not self._is_shutting_down:
                self._pending_tasks.add(key)
                self._start_generation(path, size)
            return

        self._add_to_memory(key, QPixmap.fromImage(image))
        self._pending_tasks.discard(key)
        self.thumbnailReady.emit(path)

    def invalidate(self, path: Path, *, size: QSize | None = None):
        """Removes the thumbnail from cache to force regeneration."""
        if size is None:
            size = QSize(512, 512)
        key = self._cache_key(path, size)

        if key in self._memory_cache:
            del self._memory_cache[key]

        if self._disk_cache_path is None:
            return
        disk_file = self._disk_cache_path / f"{key}.jpg"
        if disk_file.exists():
            try:
                disk_file.unlink()
            except OSError:
                pass

    def remap_album_paths(self, old_root: Path, new_root: Path, *, size: QSize | None = None) -> None:
        """Copy cached thumbnails from an album's old path to its renamed path."""

        if size is None:
            size = QSize(512, 512)
        if self._disk_cache_path is None or not new_root.exists():
            return
        try:
            paths = [path for path in new_root.rglob("*") if path.is_file()]
        except OSError:
            return

        for new_path in paths:
            try:
                rel = new_path.relative_to(new_root)
            except ValueError:
                continue
            old_path = old_root / rel
            old_key = self._cache_key(old_path, size)
            new_key = self._cache_key(new_path, size)
            if old_key in self._memory_cache and new_key not in self._memory_cache:
                self._memory_cache[new_key] = self._memory_cache[old_key]

            old_disk_file = self._disk_cache_path / f"{old_key}.jpg"
            new_disk_file = self._disk_cache_path / f"{new_key}.jpg"
            if old_disk_file.exists() and not new_disk_file.exists():
                try:
                    shutil.copy2(old_disk_file, new_disk_file)
                except OSError:
                    pass

    def _cache_key(self, path: Path, size: QSize) -> str:
        # Simple hash of path + size
        import hashlib
        s = f"{path.as_posix()}_{size.width()}x{size.height()}"
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def _add_to_memory(self, key: str, pixmap: QPixmap):
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
        elif len(self._memory_cache) >= self._max_memory_items:
            self._memory_cache.popitem(last=False)
        self._memory_cache[key] = pixmap

    def _render_thumbnail(self, path: Path, size: QSize) -> Optional[QImage]:
        if size.isEmpty() or not size.isValid():
            return None

        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
        is_video = path.suffix.lower() in video_exts
        qimage: Optional[QImage] = None
        if not is_video:
            qimage = image_loader.load_qimage(path, size)

        if qimage is None or qimage.isNull():
            pil_image = self._generator.generate(path, (size.width(), size.height()))
            if pil_image is None:
                return None
            qimage = image_loader.qimage_from_pil(pil_image)

        if qimage is None or qimage.isNull():
            return None

        if self._edit_service is not None and self._edit_service.sidecar_exists(path):
            stats = compute_color_statistics(qimage)
            state = self._edit_service.describe_adjustments(
                path,
                color_stats=stats,
            )
            adjustments = state.resolved_adjustments
        else:
            raw_adjustments = sidecar.load_adjustments(path)
            stats = compute_color_statistics(qimage) if raw_adjustments else None
            adjustments = sidecar.resolve_render_adjustments(
                raw_adjustments,
                color_stats=stats,
            )

        if adjustments:
            qimage = self._apply_geometry_and_crop(qimage, adjustments) or qimage
            qimage = apply_adjustments(qimage, adjustments, color_stats=stats)

        return self._composite_canvas(qimage, size)

    def _apply_geometry_and_crop(
        self,
        image: QImage,
        adjustments: Dict[str, float],
    ) -> Optional[QImage]:
        rotate_steps = int(adjustments.get("Crop_Rotate90", 0))
        flip_h = bool(adjustments.get("Crop_FlipH", False))
        straighten = float(adjustments.get("Crop_Straighten", 0.0))
        p_vert = float(adjustments.get("Perspective_Vertical", 0.0))
        p_horz = float(adjustments.get("Perspective_Horizontal", 0.0))

        tex_crop = (
            float(adjustments.get("Crop_CX", 0.5)),
            float(adjustments.get("Crop_CY", 0.5)),
            float(adjustments.get("Crop_W", 1.0)),
            float(adjustments.get("Crop_H", 1.0)),
        )

        log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(
            tex_crop,
            rotate_steps,
        )

        w, h = image.width(), image.height()

        if (
            rotate_steps == 0
            and not flip_h
            and abs(straighten) < 1e-5
            and abs(p_vert) < 1e-5
            and abs(p_horz) < 1e-5
            and log_w >= 0.999
            and log_h >= 0.999
        ):
            return image

        if rotate_steps % 2 == 1:
            logical_aspect = float(h) / float(w) if w > 0 else 1.0
        else:
            logical_aspect = float(w) / float(h) if h > 0 else 1.0

        matrix_inv = geo_utils.build_perspective_matrix(
            vertical=p_vert,
            horizontal=p_horz,
            image_aspect_ratio=logical_aspect,
            straighten_degrees=straighten,
            rotate_steps=0,
            flip_horizontal=flip_h,
        )

        try:
            matrix = np.linalg.inv(matrix_inv)
        except np.linalg.LinAlgError:
            matrix = np.identity(3)

        qt_perspective = QTransform(
            matrix[0, 0],
            matrix[1, 0],
            matrix[2, 0],
            matrix[0, 1],
            matrix[1, 1],
            matrix[2, 1],
            matrix[0, 2],
            matrix[1, 2],
            matrix[2, 2],
        )

        t_to_norm = QTransform().scale(1.0 / w, 1.0 / h)

        t_rot = QTransform()
        t_rot.translate(0.5, 0.5)
        t_rot.rotate(rotate_steps * 90)
        t_rot.translate(-0.5, -0.5)

        t_to_ndc = QTransform().translate(-1.0, -1.0).scale(2.0, 2.0)
        t_from_ndc = QTransform().translate(0.5, 0.5).scale(0.5, 0.5)

        log_w_px = h if rotate_steps % 2 else w
        log_h_px = w if rotate_steps % 2 else h
        t_to_pixels = QTransform().scale(log_w_px, log_h_px)

        transform = t_to_norm * t_rot * t_to_ndc * qt_perspective * t_from_ndc * t_to_pixels

        crop_x_px = log_cx * log_w_px - (log_w * log_w_px * 0.5)
        crop_y_px = log_cy * log_h_px - (log_h * log_h_px * 0.5)
        crop_w_px = log_w * log_w_px
        crop_h_px = log_h * log_h_px

        t_final = transform * QTransform().translate(-crop_x_px, -crop_y_px)

        out_w = max(1, int(round(crop_w_px)))
        out_h = max(1, int(round(crop_h_px)))

        result_img = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
        result_img.fill(Qt.transparent)

        painter = QPainter(result_img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.setTransform(t_final)
        painter.drawImage(0, 0, image)
        painter.end()

        return result_img

    def _composite_canvas(self, image: QImage, size: QSize) -> QImage:
        canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        scaled = image.scaled(
            size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        target_rect = canvas.rect()
        source_rect = scaled.rect()
        if source_rect.width() > target_rect.width():
            diff = source_rect.width() - target_rect.width()
            left = diff // 2
            right = diff - left
            source_rect.adjust(left, 0, -right, 0)
        if source_rect.height() > target_rect.height():
            diff = source_rect.height() - target_rect.height()
            top = diff // 2
            bottom = diff - top
            source_rect.adjust(0, top, 0, -bottom)
        painter.drawImage(target_rect, scaled, source_rect)
        painter.end()
        return canvas
