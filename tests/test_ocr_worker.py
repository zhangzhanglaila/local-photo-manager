"""OCR backfill skips images that were already processed."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required", exc_type=ImportError)

from iPhoto.ai.db.ocr_database import OCRRepository
from iPhoto.ai.workers.ocr_worker import OCRWorker


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list = []

    def extract_text(self, path):
        self.calls.append(path)
        return []


class _FakeRepo:
    def __init__(self, processed: set[str] | None = None) -> None:
        self.processed = set(processed or ())
        self.marked: list[tuple[str, str]] = []

    def get_processed_asset_ids(self) -> set[str]:
        return set(self.processed)

    def mark_processed(self, asset_id: str, asset_rel: str) -> None:
        self.marked.append((asset_id, asset_rel))
        self.processed.add(asset_id)

    def store_regions(self, **_kwargs) -> None:
        raise AssertionError("no regions expected")


def test_worker_skips_already_processed_assets(tmp_path) -> None:
    worker = OCRWorker(_FakeEngine(), _FakeRepo({"as_done"}), tmp_path)

    worker.enqueue_rows(
        [
            {"id": "as_done", "rel": "a.jpg"},
            {"id": "as_new", "rel": "b.jpg"},
        ]
    )

    assert [row["id"] for row in worker._queue] == ["as_new"]


def test_worker_marks_processed_assets(tmp_path) -> None:
    repo = _FakeRepo()
    worker = OCRWorker(_FakeEngine(), repo, tmp_path)

    worker._mark_processed("as_new", "b.jpg")

    assert repo.marked == [("as_new", "b.jpg")]
    assert "as_new" in worker._processed


def test_worker_reports_a_real_total_instead_of_the_attempt_count(tmp_path) -> None:
    """The task popup needs ``done/total``; the old signal repeated done as total."""

    for name in ("a.jpg", "b.jpg"):
        (tmp_path / name).write_bytes(b"x")
    worker = OCRWorker(_FakeEngine(), _FakeRepo(), tmp_path)
    worker.enqueue_rows(
        [
            {"id": "a", "rel": "a.jpg"},
            {"id": "b", "rel": "b.jpg"},
        ]
    )
    worker.finish_input()
    updates: list[tuple[int, int]] = []
    worker.signals.progress.connect(lambda done, total: updates.append((done, total)))

    worker._process_queue()

    assert updates[0] == (0, 2), "the first tick should claim the scan and show the total"
    assert updates[-1] == (2, 2)


def test_worker_can_reopen_its_input_for_a_followup_root(tmp_path) -> None:
    worker = OCRWorker(_FakeEngine(), _FakeRepo(), tmp_path)

    worker.finish_input()
    assert worker._finished is True

    worker.resume_input()
    assert worker._finished is False


def test_repository_remembers_processed_assets(tmp_path) -> None:
    repo = OCRRepository(tmp_path)
    repo.mark_processed("as_1", "a.jpg")

    assert repo.get_processed_asset_ids() == {"as_1"}

    repo.delete_by_asset("as_1")
    assert repo.get_processed_asset_ids() == set()
    repo.close()
