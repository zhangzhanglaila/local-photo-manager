"""The scan workers must receive the services bound to the open library.

``ScanCoordinatorMixin`` used to read ``getattr(self, "_scan_service")`` and
``getattr(self, "_people_service")``; the host only ever stores ``self._services``
(``LibraryServiceRegistry``), so both were always ``None`` and every worker fell
back to constructing its own service for the wrong (unbound) root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore", reason="the coordinator is a Qt mixin")

from PySide6.QtCore import QObject, Signal

from iPhoto.library import scan_coordinator as module
from iPhoto.library.scan_coordinator import ScanCoordinatorMixin
from iPhoto.library.service_registry import LibraryServiceRegistry


class _Host(QObject, ScanCoordinatorMixin):
    scanProgress = Signal(object, int, int)
    scanFinished = Signal(object, bool)
    scanChunkReady = Signal(object, list)
    scanError = Signal(object, str)
    scanBatchFailed = Signal(object, int)
    faceScanStatusChanged = Signal(str)
    faceDownloadProgress = Signal(int, int)
    ocrScanStatusChanged = Signal(object)
    aiScanStarted = Signal(str)
    aiScanProgress = Signal(str, int, int)
    aiScanFinished = Signal(str, bool)
    aiScanCancelled = Signal(str)

    def __init__(self, root: Path, registry: LibraryServiceRegistry) -> None:
        QObject.__init__(self)
        self._root = root
        self._services = registry
        self._scan_service = "stale-name-should-not-be-used"
        self._people_service = "stale-name-should-not-be-used"
        self._live_scan_buffer = {}
        self._current_scanner_worker = None
        self._scan_generation = 0
        self._current_face_scanner = None
        self._current_ocr_scanner = None
        self._live_scan_root = None
        self._face_scan_status_message = None
        from PySide6.QtCore import QMutex

        self._scan_buffer_lock = QMutex()

        class _Pool:
            def start(self, worker) -> None:
                pass

        self._scan_thread_pool = _Pool()
        self._face_scan_pending_start = False
        self._ocr_scan_pending_start = False
        self._face_scan_started = False
        self._ocr_scan_started = False

    # Host API used by the mixin
    def root(self) -> Path:
        return self._root

    def invalidate_geotagged_assets_cache(self, **_kwargs) -> None:
        pass

    def _paths_equal(self, left, right) -> bool:  # noqa: D102 - host contract
        from iPhoto.utils.pathutils import paths_equal

        return paths_equal(left, right)

    def _paths_equal_root(self, left, right):  # pragma: no cover - compatibility
        return self._paths_equal(left, right)


@pytest.fixture()
def host(tmp_path, monkeypatch, qapp) -> _Host:
    registry = LibraryServiceRegistry(
        scan_service=object(), people_service=object(), asset_query_service=object()
    )
    widget = _Host(tmp_path, registry)

    recorded: dict[str, object] = {}
    widget._recorded = recorded  # type: ignore[attr-defined]

    class _FakeScannerWorker(QObject):
        def __init__(self, root, include, exclude, signals, *, library_root=None, scan_service=None):
            super().__init__()
            recorded["scan_service"] = scan_service
            self._signals = signals

        def cancel(self) -> None:
            recorded["cancelled"] = True

    class _FakeFaceWorker(QObject):
        statusChanged = Signal(str)
        downloadProgress = Signal(int, int)
        finished = Signal(object, bool)

        def __init__(
            self,
            library_root,
            parent=None,
            *,
            people_service=None,
            model_pack=None,
        ):
            super().__init__()
            recorded["people_service"] = people_service
            recorded["model_pack"] = model_pack

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(module, "ScannerWorker", _FakeScannerWorker)
    monkeypatch.setattr(module, "FaceScanWorker", _FakeFaceWorker)
    monkeypatch.setattr(
        _Host, "_create_ocr_worker", lambda self, root: None, raising=False
    )
    return widget


@pytest.fixture(scope="module")
def qapp():
    import os

    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def test_workers_get_the_registry_services(host: _Host) -> None:
    host.start_scanning(host.root(), ["**/*"], [])

    recorded = host._recorded  # type: ignore[attr-defined]
    assert recorded.get("scan_service") is host._services.scan_service
    assert recorded.get("people_service") is host._services.people_service
    # The stale names must not be consulted at all.
    assert recorded.get("scan_service") != "stale-name-should-not-be-used"
    assert recorded.get("people_service") != "stale-name-should-not-be-used"


def test_the_mixin_no_longer_reads_stale_attribute_names() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert 'getattr(self, "_scan_service"' not in source
    assert 'getattr(self, "_people_service"' not in source


def test_a_cancelled_scanner_cannot_finish_the_new_scan(host) -> None:
    """Queued signals from an old scan used to start the new OCR worker empty."""

    from PySide6.QtTest import QSignalSpy

    from iPhoto.library.workers.scanner_worker import ScannerSignals

    new_worker = object()
    old_worker = object()
    host._current_scanner_worker = new_worker
    spy = QSignalSpy(host.scanFinished)

    old_signals = ScannerSignals()
    old_signals._worker = old_worker
    old_signals.finished.connect(host._on_scan_finished)
    old_signals.finished.emit(host.root(), [])

    assert spy.count() == 0, "the stale scan must not emit scanFinished"
    assert host._current_scanner_worker is new_worker


def test_a_cancelled_scanner_cannot_feed_the_new_ai_queue(host) -> None:
    from iPhoto.library.workers.scanner_worker import ScannerSignals

    class _Ai:
        def __init__(self) -> None:
            self.rows: list = []

        def enqueue_rows(self, rows) -> None:
            self.rows.extend(rows)

    new_worker = object()
    host._current_scanner_worker = new_worker
    host._current_face_scanner = _Ai()
    host._current_ocr_scanner = _Ai()

    old_signals = ScannerSignals()
    old_signals._worker = object()
    old_signals.chunkReady.connect(host._on_scan_chunk)
    old_signals.chunkReady.emit(host.root(), [{"id": "old", "rel": "old.jpg"}])

    assert host._current_face_scanner.rows == []
    assert host._current_ocr_scanner.rows == []


def test_a_cancelled_ocr_worker_is_not_reported_as_complete(host) -> None:
    from PySide6.QtTest import QSignalSpy

    class _Ocr:
        cancelled = True

    worker = _Ocr()
    host._current_ocr_scanner = worker
    statuses = QSignalSpy(host.ocrScanStatusChanged)

    host._on_ocr_finished(12, 0, worker)

    assert host._current_ocr_scanner is None
    emitted = [statuses.at(index)[0].key for index in range(statuses.count())]
    assert emitted == [""], (
        "cancellation must clear the line, not announce status.ocr.done"
    )


def test_a_stale_ocr_worker_cannot_finish_the_new_one(host) -> None:
    from PySide6.QtTest import QSignalSpy

    new_worker = object()
    host._current_ocr_scanner = new_worker
    statuses = QSignalSpy(host.ocrScanStatusChanged)

    host._on_ocr_finished(12, 0, object())

    assert host._current_ocr_scanner is new_worker
    assert statuses.count() == 0


def test_sequential_root_scans_share_ai_workers_and_start_once(host, monkeypatch) -> None:
    """Startup scans the primary root and then each extra root.

    The first root used to start both AI workers immediately; the next root then
    replaced them, so their queued rows were discarded and their task rows were
    left "waiting" because the replacement made every finish signal look stale.
    """

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtTest import QSignalSpy

    started_workers: list[object] = []

    class _ScanService:
        def finalize_scan_result(self, *_args, **_kwargs) -> None:
            pass

    class _FakeScanner(QObject):
        def __init__(self, root, include, exclude, signals, *, library_root=None, scan_service=None):
            super().__init__()
            self.root = root
            self.signals = signals
            self.scan_service = scan_service
            self.cancelled = False
            self.failed = False

        def cancel(self) -> None:
            self.cancelled = True

    class _FakeFace(QObject):
        statusChanged = Signal(str)
        downloadProgress = Signal(int, int)
        progressChanged = Signal(int, int)
        finished = Signal()

        def __init__(self, library_root, parent=None, *, people_service=None, model_pack=None):
            super().__init__()
            self.input_closed = False
            self.cancelled = False
            self.started = False
            self.rows: list = []

        def enqueue_rows(self, rows) -> None:
            self.rows.extend(rows)

        def cancel(self) -> None:
            self.cancelled = True

        def finish_input(self) -> None:
            self.input_closed = True

        def resume_input(self) -> None:
            self.input_closed = False

        def start(self, _priority=None) -> None:
            self.started = True
            started_workers.append(self)

    class _OcrSignals(QObject):
        progress = Signal(int, int)
        finished = Signal(int, int)
        error = Signal(str)

    class _FakeOcr:
        def __init__(self) -> None:
            self.signals = _OcrSignals()
            self._queue: list = []
            self.input_closed = False
            self.cancelled = False
            self.started = False

        def enqueue_rows(self, rows) -> None:
            self._queue.extend(rows)

        def cancel(self) -> None:
            self.cancelled = True

        def finish_input(self) -> None:
            self.input_closed = True

        def resume_input(self) -> None:
            self.input_closed = False

    class _Pool:
        def start(self, worker) -> None:
            if isinstance(worker, _FakeOcr):
                worker.started = True
                started_workers.append(worker)

    extra_root = host.root().parent / "extra-root"
    fake_ocr = _FakeOcr()
    scan_service = _ScanService()
    monkeypatch.setattr(module, "ScannerWorker", _FakeScanner)
    monkeypatch.setattr(module, "FaceScanWorker", _FakeFace)
    host._create_ocr_worker = lambda _root: fake_ocr
    host._scan_thread_pool = _Pool()

    started = QSignalSpy(host.aiScanStarted)
    cancelled = QSignalSpy(host.aiScanCancelled)

    def _start_extra(finished_root, _ok) -> None:
        if finished_root == host.root():
            host.start_scanning(extra_root, ["**/*"], [])

    host.scanFinished.connect(_start_extra)
    host.start_scanning(host.root(), ["**/*"], [])

    primary_scanner = host._current_scanner_worker
    face = host._current_face_scanner
    host._on_scan_chunk(host.root(), [{"id": "a", "rel": "a.jpg"}])
    primary_scanner.scan_service = scan_service
    primary_scanner.signals.finished.emit(host.root(), [])

    assert started.count() == 0, "the first root must not start AI before the next root"
    assert cancelled.count() == 0
    assert host._current_face_scanner is face
    assert host._current_ocr_scanner is fake_ocr
    assert host._face_scan_pending_start is True
    assert host._ocr_scan_pending_start is True

    extra_scanner = host._current_scanner_worker
    host._on_scan_chunk(extra_root, [{"id": "b", "rel": "b.jpg"}])
    extra_scanner.scan_service = scan_service
    extra_scanner.signals.finished.emit(extra_root, [])

    assert [started.at(index)[0] for index in range(started.count())] == [
        "face",
        "ocr",
    ]
    assert face.started is True
    assert fake_ocr.started is True
    assert face.input_closed is True
    assert fake_ocr.input_closed is True
    assert [row["id"] for row in fake_ocr._queue] == ["a", "b"]
    assert [row["id"] for row in face.rows] == ["a", "b"]


def test_a_new_scan_reuses_a_running_ai_worker_instead_of_restarting_it(host, monkeypatch) -> None:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtTest import QSignalSpy

    class _ScanService:
        def finalize_scan_result(self, *_args, **_kwargs) -> None:
            pass

    class _FakeScanner(QObject):
        def __init__(self, root, include, exclude, signals, *, library_root=None, scan_service=None):
            super().__init__()
            self.root = root
            self.signals = signals
            self.scan_service = scan_service
            self.cancelled = False
            self.failed = False

        def cancel(self) -> None:
            self.cancelled = True

    class _FakeFace(QObject):
        statusChanged = Signal(str)
        downloadProgress = Signal(int, int)
        progressChanged = Signal(int, int)
        finished = Signal()

        def __init__(self, library_root, parent=None, *, people_service=None, model_pack=None):
            super().__init__()
            self.started = 0
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def finish_input(self) -> None:
            pass

        def resume_input(self) -> None:
            pass

        def start(self, _priority=None) -> None:
            self.started += 1

    class _OcrSignals(QObject):
        progress = Signal(int, int)
        finished = Signal(int, int)
        error = Signal(str)

    class _FakeOcr:
        def __init__(self) -> None:
            self.signals = _OcrSignals()
            self._queue: list = []
            self.started = 0
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def finish_input(self) -> None:
            pass

        def resume_input(self) -> None:
            pass

    class _Pool:
        def start(self, worker) -> None:
            if isinstance(worker, _FakeOcr):
                worker.started += 1

    fake_ocr = _FakeOcr()
    scan_service = _ScanService()
    monkeypatch.setattr(module, "ScannerWorker", _FakeScanner)
    monkeypatch.setattr(module, "FaceScanWorker", _FakeFace)
    host._create_ocr_worker = lambda _root: fake_ocr
    host._scan_thread_pool = _Pool()

    started = QSignalSpy(host.aiScanStarted)
    cancelled = QSignalSpy(host.aiScanCancelled)

    host.start_scanning(host.root(), ["**/*"], [])
    host._current_scanner_worker.scan_service = scan_service
    host._current_scanner_worker.signals.finished.emit(host.root(), [])

    assert [started.at(index)[0] for index in range(started.count())] == ["face", "ocr"]
    face = host._current_face_scanner
    face.started = 1

    host.start_scanning(host.root().parent / "second-root", ["**/*"], [])
    host._current_scanner_worker.scan_service = scan_service
    host._current_scanner_worker.signals.finished.emit(
        host.root().parent / "second-root", []
    )

    assert cancelled.count() == 0, "a follow-up scan must not cancel a running AI task"
    assert host._current_face_scanner is face
    assert face.started == 1
    assert fake_ocr.started == 1


def test_stopping_a_scan_removes_waiting_ai_tasks(host, monkeypatch) -> None:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtTest import QSignalSpy

    class _ScanService:
        def finalize_scan_result(self, *_args, **_kwargs) -> None:
            pass

    class _FakeScanner(QObject):
        def __init__(self, root, include, exclude, signals, *, library_root=None, scan_service=None):
            super().__init__()
            self.signals = signals
            self.scan_service = scan_service
            self.cancelled = False
            self.failed = False

        def cancel(self) -> None:
            self.cancelled = True

    class _FakeFace(QObject):
        statusChanged = Signal(str)
        downloadProgress = Signal(int, int)
        progressChanged = Signal(int, int)
        finished = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def finish_input(self) -> None:
            pass

        def start(self, _priority=None) -> None:
            pass

    class _OcrSignals(QObject):
        progress = Signal(int, int)
        finished = Signal(int, int)
        error = Signal(str)

    class _FakeOcr:
        def __init__(self) -> None:
            self.signals = _OcrSignals()
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def finish_input(self) -> None:
            pass

    fake_ocr = _FakeOcr()
    monkeypatch.setattr(module, "ScannerWorker", _FakeScanner)
    monkeypatch.setattr(module, "FaceScanWorker", _FakeFace)
    host._create_ocr_worker = lambda _root: fake_ocr
    host._scan_thread_pool = type(
        "_Pool", (), {"start": lambda self, worker: None}
    )()

    cancelled = QSignalSpy(host.aiScanCancelled)
    host.start_scanning(host.root(), ["**/*"], [])

    host.stop_scanning()

    assert [cancelled.at(index)[0] for index in range(cancelled.count())] == [
        "face",
        "ocr",
    ]
    assert host._current_face_scanner is None
    assert host._current_ocr_scanner is None
