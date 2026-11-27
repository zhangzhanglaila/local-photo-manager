"""Duplicate-detection and smart-album jobs run off the GUI thread."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from iPhoto.gui.services import organize_workers


class _RecordingPool:
    """Stand-in for ``QThreadPool`` that keeps started jobs for manual running."""

    def __init__(self) -> None:
        self.jobs: list = []

    def start(self, job) -> None:
        self.jobs.append(job)

    def run_all(self) -> None:
        assert len(self.jobs) == 1, f"expected one job, got {len(self.jobs)}"
        self.jobs[0].run()


def _session(root: Path = Path("/library")) -> SimpleNamespace:
    return SimpleNamespace(
        library_root=root,
        asset_runtime=SimpleNamespace(assets=object()),
    )


def test_build_organize_service_binds_session_assets_and_root(monkeypatch) -> None:
    session = _session()
    factory = MagicMock()
    monkeypatch.setattr(organize_workers, "OrganizeService", factory)

    built = organize_workers.build_organize_service(session)

    factory.assert_called_once_with(
        asset_repository=session.asset_runtime.assets,
        library_root=session.library_root,
    )
    assert built is factory.return_value


def test_find_duplicates_reports_groups_with_the_duplicate_threshold(monkeypatch) -> None:
    service = MagicMock()
    service.find_duplicates.return_value = ["group-a"]
    monkeypatch.setattr(organize_workers, "build_organize_service", lambda _s: service)
    pool = _RecordingPool()
    results: list[list] = []

    organize_workers.find_duplicates(_session(), results.append, pool=pool)
    pool.run_all()

    service.find_duplicates.assert_called_once_with(
        threshold=organize_workers.DUPLICATE_SIMILARITY_THRESHOLD
    )
    assert results == [["group-a"]]


def test_create_smart_albums_forwards_group_by(monkeypatch) -> None:
    service = MagicMock()
    service.create_smart_albums.return_value = ["album-a"]
    monkeypatch.setattr(organize_workers, "build_organize_service", lambda _s: service)
    pool = _RecordingPool()
    results: list[list] = []

    organize_workers.create_smart_albums(_session(), "location", results.append, pool=pool)
    pool.run_all()

    service.create_smart_albums.assert_called_once_with(group_by="location")
    assert results == [["album-a"]]


@pytest.mark.parametrize("action", ["duplicates", "albums"])
def test_failing_job_reports_no_results(monkeypatch, action: str) -> None:
    service = MagicMock()
    service.find_duplicates.side_effect = RuntimeError("boom")
    service.create_smart_albums.side_effect = RuntimeError("boom")
    monkeypatch.setattr(organize_workers, "build_organize_service", lambda _s: service)
    pool = _RecordingPool()
    results: list[list] = []

    if action == "duplicates":
        organize_workers.find_duplicates(_session(), results.append, pool=pool)
    else:
        organize_workers.create_smart_albums(_session(), "time", results.append, pool=pool)
    pool.run_all()

    assert results == [[]]
