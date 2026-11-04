"""Background AI scans must step aside while the user is looking at something.

Face and OCR inference read full-size images and run on onnxruntime. Leaving them
running while a photo is on screen is what made viewing and scrolling feel
sluggish, so the app registers "the user is viewing media" and the scans pause
until it clears.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtCore", reason="the scan coordinator is a Qt mixin")

from PySide6.QtCore import QCoreApplication

from iPhoto.library.scan_coordinator import ScanCoordinatorMixin


class _Scanner:
    def __init__(self, calls: list, name: str) -> None:
        self._calls = calls
        self._name = name
        self._running = True

    def isRunning(self) -> bool:  # noqa: N802 - Qt naming
        return self._running

    def set_video_playing(self, value: bool) -> None:
        self._calls.append((self._name, value))


class _Ocr:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def set_paused(self, value: bool) -> None:
        self._calls.append(("ocr", value))


def test_foreground_viewing_pauses_face_and_ocr_scans() -> None:
    calls: list[tuple[str, bool]] = []
    coordinator = SimpleNamespace(
        _current_face_scanner=_Scanner(calls, "face"),
        _current_ocr_scanner=_Ocr(calls),
    )

    ScanCoordinatorMixin.set_video_playing(coordinator, True)
    ScanCoordinatorMixin.set_video_playing(coordinator, False)

    assert calls == [("face", True), ("ocr", True), ("face", False), ("ocr", False)]


def _ocr_worker(tmp_path, processed: list[str]):
    from iPhoto.ai.workers.ocr_worker import OCRWorker

    class _Engine:
        def extract_text(self, path):
            processed.append(str(path))
            return []

    class _Repo:
        def store_regions(self, **kwargs):
            del kwargs

    (tmp_path / "photo.jpg").write_bytes(b"x")
    return OCRWorker(_Engine(), _Repo(), library_root=tmp_path)


def test_ocr_worker_honours_pause(tmp_path) -> None:
    """A paused OCR worker must not consume queued rows."""

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    processed: list[str] = []
    worker = _ocr_worker(tmp_path, processed)

    worker.set_paused(True)
    worker.enqueue_rows([{"id": "1", "rel": "photo.jpg"}])
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    time.sleep(0.6)
    worker.cancel()
    thread.join(3.0)

    assert processed == [], "a paused worker must not process queued rows"


def test_ocr_worker_resumes_after_pause(tmp_path) -> None:
    """The harness itself works: without a pause the row is processed."""

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    processed: list[str] = []
    worker = _ocr_worker(tmp_path, processed)

    worker.enqueue_rows([{"id": "1", "rel": "photo.jpg"}])
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3.0
    while not processed and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.cancel()
    thread.join(3.0)

    assert processed, "an unpaused worker should process the queued row"
