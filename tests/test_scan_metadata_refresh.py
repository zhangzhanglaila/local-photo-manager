"""Metadata freshness of the incremental index, tracked per file.

The incremental scan reuses an existing row when the file's size and mtime are
unchanged. Rows produced by an older extractor (for example one that could not
parse GPS) must still be repaired, so the extractor version is recorded per
library-relative path rather than as one stamp for the whole library:

* a row whose recorded version is older is dropped from the cache and re-read;
* a row with the current version keeps the fast path;
* refreshing one album re-stamps only that album's rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from iPhoto.bootstrap import library_scan_service as scan_service_module
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.config import SCANNER_METADATA_VERSION


class _FakeRepository:
    def __init__(self, versions: dict[str, int] | None = None) -> None:
        self.versions: dict[str, int] = dict(versions or {})
        self.stamped: dict[str, int] = {}

    def rels_needing_extraction(self, rels, version: int) -> set[str]:
        return {rel for rel in rels if self.versions.get(rel, 0) < int(version)}

    def stamp_extraction_versions(self, rels, version: int) -> None:
        for rel in rels:
            if rel:
                self.stamped[rel] = int(version)
                self.versions[rel] = int(version)

    def read_album_assets(self, album_path, **_kwargs):
        return []

    def read_all(self, **_kwargs):
        return []

    def merge_scan_rows(self, rows):  # pragma: no cover - not used by these tests
        return list(rows)


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


def _service(
    tmp_path: Path, versions: dict[str, int] | None = None
) -> tuple[LibraryScanService, _FakeScanner, _FakeRepository]:
    scanner = _FakeScanner()
    repository = _FakeRepository(versions)
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


def test_row_from_an_older_extractor_is_re_read(
    tmp_path: Path,
    cache_calls: list[Path],
) -> None:
    service, scanner, repository = _service(
        tmp_path, versions={"album/photo.jpg": SCANNER_METADATA_VERSION - 1}
    )

    service.scan_album(tmp_path, reuse_existing_index=True)

    assert len(cache_calls) == 1, "the cache is still consulted"
    assert scanner.existing_indexes == [{}], "the stale row is dropped before scanning"
    assert repository.stamped["album/photo.jpg"] == SCANNER_METADATA_VERSION


def test_current_rows_keep_the_fast_path(
    tmp_path: Path,
    cache_calls: list[Path],
) -> None:
    service, scanner, _repository = _service(
        tmp_path, versions={"album/photo.jpg": SCANNER_METADATA_VERSION}
    )

    service.scan_album(tmp_path, reuse_existing_index=True)

    assert scanner.existing_indexes == [
        {"album/photo.jpg": {"rel": "album/photo.jpg", "bytes": 1, "ts": 1}}
    ], "an up-to-date row is reused as-is"


def test_rescan_always_bypasses_the_cache(
    tmp_path: Path,
    cache_calls: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _scanner, _repository = _service(tmp_path)
    captured: dict[str, Any] = {}

    def _scan_album(*args, **kwargs):
        captured.update(kwargs)
        from iPhoto.application.use_cases.scan_library import ScanLibraryResult

        return ScanLibraryResult(rows=[])

    monkeypatch.setattr(service, "scan_album", _scan_album)
    monkeypatch.setattr(service, "finalize_scan_result", lambda *a, **k: [])

    service.rescan_album(tmp_path)

    assert captured["reuse_existing_index"] is False


def test_refreshing_one_album_stamps_only_its_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service, _scanner, repository = _service(library_root, versions={})

    service.scan_album(album_root, reuse_existing_index=True)

    # This hook receives already library-relative rows.
    assert repository.stamped == {"album/album/photo.jpg": SCANNER_METADATA_VERSION}


def test_scan_finished_hook_stamps_the_scanned_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Qt scanner worker finishes through ``finalize_scan_result``.

    Its ``scan_album`` return path can already be flagged as cancelled by the time
    the scan is done, so the version must not depend on that path.
    """

    from iPhoto.bootstrap.library_asset_lifecycle_service import (
        LibraryAssetLifecycleService,
    )

    service, _scanner, repository = _service(tmp_path)
    monkeypatch.setattr(service, "finalize_scan", lambda *a, **k: None)
    monkeypatch.setattr(service, "pair_album", lambda *a, **k: None)
    monkeypatch.setattr(
        LibraryAssetLifecycleService,
        "reconcile_missing_scan_rows",
        lambda *a, **k: 0,
    )

    service.finalize_scan_result(tmp_path, rows=[{"rel": "album/photo.jpg"}])

    # This hook receives already library-relative rows.
    assert repository.stamped == {"album/photo.jpg": SCANNER_METADATA_VERSION}
