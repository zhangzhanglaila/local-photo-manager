from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
pytest.importorskip("PySide6", reason="PySide6 is required for thumbnail tests", exc_type=ImportError)
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


def test_thumbnail_cache_service_remaps_album_disk_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir)
    old_album = tmp_path / "Trips"
    new_album = tmp_path / "Renamed Trips"
    old_photo = old_album / "photo.jpg"
    new_photo = new_album / "photo.jpg"
    new_photo.parent.mkdir(parents=True)
    new_photo.write_bytes(b"image")

    size = QSize(512, 512)
    old_key = service._cache_key(old_photo, size)
    new_key = service._cache_key(new_photo, size)
    old_cache_file = cache_dir / f"{old_key}.jpg"
    new_cache_file = cache_dir / f"{new_key}.jpg"
    old_cache_file.write_bytes(b"cached-thumbnail")

    service.remap_album_paths(old_album, new_album, size=size)

    assert new_cache_file.read_bytes() == b"cached-thumbnail"


def test_render_thumbnail_skips_color_stats_without_sidecar(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = False
    service.set_edit_service(edit_service)
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"image")
    size = QSize(64, 64)

    with patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.image_loader.load_qimage",
        return_value=image,
    ), patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.compute_color_statistics",
    ) as compute_stats:
        rendered = service._render_thumbnail(path, size)

    assert rendered is not None
    edit_service.describe_adjustments.assert_not_called()
    compute_stats.assert_not_called()


class _InlinePool:
    """Run queued QRunnables synchronously so cache hits complete in the test."""

    def start(self, worker) -> None:
        worker.run()


def _cached_thumbnail_file(service: ThumbnailCacheService, photo: Path, size: QSize) -> Path:
    return service._disk_cache_path / f"{service._cache_key(photo, size)}.jpg"


def test_disk_cache_hit_is_not_reencoded_or_rewritten(tmp_path: Path) -> None:
    """Loading a cached thumbnail must not re-encode and overwrite it.

    The disk-load path used to share the generation handler, so every cache hit
    decoded the JPEG, re-encoded it and wrote it back on the GUI thread.
    """

    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._thread_pool = _InlinePool()  # type: ignore[assignment]
    size = QSize(256, 256)
    photo = tmp_path / "photo.jpg"
    cache_file = _cached_thumbnail_file(service, photo, size)

    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    assert image.save(str(cache_file), "JPEG", 95)
    before_bytes = cache_file.read_bytes()
    before_mtime = cache_file.stat().st_mtime_ns

    ready: list[Path] = []
    service.thumbnailReady.connect(ready.append)

    assert service.get_thumbnail(photo, size) is None  # filled asynchronously
    assert ready == [photo]
    assert cache_file.read_bytes() == before_bytes
    assert cache_file.stat().st_mtime_ns == before_mtime


def test_generated_thumbnail_is_written_to_disk(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._thread_pool = _InlinePool()  # type: ignore[assignment]
    size = QSize(64, 64)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0)
    service._render_thumbnail = lambda *_a, **_k: image  # type: ignore[method-assign]

    service.get_thumbnail(photo, size)

    assert _cached_thumbnail_file(service, photo, size).exists()


def test_corrupt_disk_cache_entry_is_regenerated(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._thread_pool = _InlinePool()  # type: ignore[assignment]
    size = QSize(64, 64)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    cache_file = _cached_thumbnail_file(service, photo, size)
    cache_file.write_bytes(b"not a jpeg")

    rendered: list[Path] = []
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0)

    def _render(path: Path, _size: QSize) -> QImage:
        rendered.append(path)
        return image

    service._render_thumbnail = _render  # type: ignore[method-assign]

    service.get_thumbnail(photo, size)

    assert rendered == [photo]
    assert cache_file.exists()
    assert cache_file.read_bytes() != b"not a jpeg"

