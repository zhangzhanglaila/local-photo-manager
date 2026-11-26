"""Coverage numbers shown in the settings page."""

from __future__ import annotations

from pathlib import Path

from iPhoto.agent.services import search_coverage
from iPhoto.cache.index_store import IndexStore


def _make_index(path: Path, *, photos: int, videos: int = 3) -> None:
    """Write a real index so the photo filter is exercised as in production."""

    store = IndexStore(path)
    try:
        store.write_rows(
            [
                {"rel": f"p{i}.jpg", "id": f"p{i}", "media_type": 0, "live_role": 0}
                for i in range(photos)
            ]
            + [
                {"rel": f"v{i}.mp4", "id": f"v{i}", "media_type": 1, "live_role": 0}
                for i in range(videos)
            ]
            + [
                # Live photo motion clip: a second row with a non-null role.
                {"rel": "p0.mov", "id": "p0-motion", "media_type": 0, "live_role": 1}
            ]
        )
    finally:
        store.close()


def test_coverage_counts_photos_and_one_model(tmp_path, monkeypatch):
    _make_index(tmp_path, photos=5)

    monkeypatch.setattr(search_coverage, "count_indexed", lambda root, model: 3)

    assert search_coverage.count_photos(tmp_path) == 5
    assert search_coverage.compute_coverage(tmp_path, "siglip2-base-patch16-224") == (3, 5)


def test_missing_database_reports_zero_and_stays_missing(tmp_path):
    assert search_coverage.count_photos(tmp_path) == 0
    assert not (tmp_path / ".iPhoto" / "global_index.db").exists()
