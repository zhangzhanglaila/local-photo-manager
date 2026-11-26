"""Tests for the runtime-owned library asset services."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.cache.index_store import reset_global_repository
from iPhoto.cache.index_store.repository import AssetRepository
from iPhoto.errors import LibraryUnavailableError
from iPhoto.infrastructure.services.library_asset_runtime import LibraryAssetRuntime


@pytest.fixture(autouse=True)
def _reset_global_index() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


def test_unbound_runtime_never_touches_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boot-time runtime must not create ``~/.iPhoto`` before a library exists."""

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    runtime = LibraryAssetRuntime()

    assert runtime.is_bound is False
    assert runtime.thumbnail_service._disk_cache_path is None
    assert not (fake_home / ".iPhoto").exists()
    with pytest.raises(LibraryUnavailableError):
        _ = runtime.assets
    with pytest.raises(LibraryUnavailableError):
        _ = runtime.repository


def test_unbound_runtime_serves_no_thumbnails(tmp_path: Path) -> None:
    from PySide6.QtCore import QSize

    runtime = LibraryAssetRuntime()

    assert runtime.thumbnail_service.get_thumbnail(tmp_path / "photo.jpg", QSize(64, 64)) is None


def test_bind_library_root_none_unbinds(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    runtime = LibraryAssetRuntime(library_root)
    assert runtime.is_bound is True

    runtime.bind_library_root(None)

    assert runtime.is_bound is False
    assert runtime.thumbnail_service._disk_cache_path is None
    with pytest.raises(LibraryUnavailableError):
        _ = runtime.assets


def test_bind_library_root_rebuilds_repo_and_cache_path(tmp_path: Path) -> None:
    initial_root = tmp_path / "initial"
    initial_root.mkdir()
    runtime = LibraryAssetRuntime(initial_root)
    initial_repository = runtime.repository
    initial_assets = runtime.assets

    library_root = tmp_path / "library"
    library_root.mkdir()

    runtime.bind_library_root(library_root)

    assert runtime.repository is not initial_repository
    assert runtime.assets is not initial_assets
    assert runtime.repository is runtime.assets
    assert isinstance(runtime.repository, AssetRepository)
    assert runtime.thumbnail_service._disk_cache_path == (
        library_root / ".iPhoto" / "cache" / "thumbs"
    )

    runtime.shutdown()


def test_bind_library_root_uses_existing_legacy_work_dir(tmp_path: Path) -> None:
    initial_root = tmp_path / "initial"
    initial_root.mkdir()
    runtime = LibraryAssetRuntime(initial_root)
    library_root = tmp_path / "library"
    legacy_work_dir = library_root / ".iphoto"
    legacy_work_dir.mkdir(parents=True)

    runtime.bind_library_root(library_root)

    assert runtime.thumbnail_service._disk_cache_path == (
        legacy_work_dir / "cache" / "thumbs"
    )
    assert runtime.assets.path == legacy_work_dir / "global_index.db"
    assert runtime.repository is runtime.assets

    runtime.shutdown()
