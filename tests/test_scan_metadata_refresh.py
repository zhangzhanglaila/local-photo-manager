"""Metadata freshness of the incremental index.

The incremental scan reuses an existing row when the file's size and mtime are
unchanged. That keeps startup fast, but it also means a row written by an older
extractor (for example one that could not parse GPS) is never repaired by simply
restarting or rescanning. These tests pin the two escape hatches:

* an index stamped with an older extractor version is re-extracted once, and
* an explicit rescan always re-extracts, ignoring the cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from iPhoto.bootstrap import library_scan_service as scan_service_module
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.config import SCANNER_METADATA_VERSION


class _FakeRepository:
    def __init__(self, version: int) -> None:
        self.version = version
        self.merged: list[dict[str, Any]] = []

    def metadata_version(self) -> int:
        return self.version

    def set_metadata_version(self, version: int) -> None:
        self.version = version

    def merge_scan_rows(self, rows):  # pragma: no cover - not used by these tests
        materialized = list(rows)
        self.merged.extend(materialized)
        return materialized


class _FakeScanner:
    def __init__(self) -> None:
        self.existing_indexes: list[dict[str, Any] | None] = []

    def scan(
        self,
        root: Path,
        include,
        exclude,
        *,
        existing_index=None,
        progress_callback=None,
    ) -> Iterator[dict[str, Any]]:
        self.existing_indexes.append(existing_index)
        yield {
            "rel": "album/photo.jpg",
            "bytes": 1,
            "ts": 1,
            "id": "as_1",
            "gps": {"lat": 44.5, "lon": 81.3},
        }


def _service(tmp_path: Path, version: int) -> tuple[LibraryScanService, _FakeScanner, _FakeRepository]:
    scanner = _FakeScanner()
    repository = _FakeRepository(version)
    service = LibraryScanService(
        tmp_path,
        scanner=scanner,
        repository_factory=lambda _root: repository,
    )
    return service, scanner, repository


@pytest.fixture()
def cache_calls(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record incremental-cache lookups instead of touching a real index."""

    calls: list[Path] = []

    def _loader(scan_root, *, library_root, repository):
        calls.append(Path(scan_root))
        return {"album/photo.jpg": {"rel": "album/photo.jpg", "bytes": 1, "ts": 1}}

    monkeypatch.setattr(scan_service_module, "load_incremental_index_cache", _loader)
    return calls


def test_stale_index_version_forces_re_extraction(
    tmp_path: Path,
    cache_calls: list[Path],
) -> None:
    service, _scanner, repository = _service(tmp_path, version=SCANNER_METADATA_VERSION - 1)

    service.scan_album(tmp_path, reuse_existing_index=True)

    assert cache_calls == [], "a stale index must not be reused"
    assert repository.version == SCANNER_METADATA_VERSION, "the refresh must be stamped"


def test_current_index_version_still_reuses_cached_rows(
    tmp_path: Path,
    cache_calls: list[Path],
) -> None:
    service, _scanner, repository = _service(tmp_path, version=SCANNER_METADATA_VERSION)

    service.scan_album(tmp_path, reuse_existing_index=True)

    assert len(cache_calls) == 1, "an up-to-date index keeps the fast path"
    assert repository.version == SCANNER_METADATA_VERSION


def test_rescan_always_bypasses_the_cache(
    tmp_path: Path,
    cache_calls: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _scanner, _repository = _service(tmp_path, version=SCANNER_METADATA_VERSION)
    captured: dict[str, Any] = {}

    def _scan_album(*args, **kwargs):
        captured.update(kwargs)
        from iPhoto.application.use_cases.scan_library import ScanLibraryResult

        return ScanLibraryResult(rows=[])

    monkeypatch.setattr(service, "scan_album", _scan_album)
    monkeypatch.setattr(service, "finalize_scan_result", lambda *a, **k: [])

    service.rescan_album(tmp_path)

    assert captured["reuse_existing_index"] is False


def test_album_scoped_refresh_does_not_stamp_the_library(
    tmp_path: Path,
    cache_calls: list[Path],
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service, _scanner, repository = _service(library_root, version=SCANNER_METADATA_VERSION - 1)

    service.scan_album(album_root, reuse_existing_index=True)

    # The album was refreshed, but the rest of the library was not, so the index
    # must keep advertising itself as stale.
    assert repository.version == SCANNER_METADATA_VERSION - 1
