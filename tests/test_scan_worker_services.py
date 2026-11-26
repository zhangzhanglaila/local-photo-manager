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
    scanError = Signal(object, str)
    scanBatchFailed = Signal(object, int)
    faceScanStatusChanged = Signal(str)
    faceDownloadProgress = Signal(int, int)
    ocrScanStatusChanged = Signal(str)

    def __init__(self, root: Path, registry: LibraryServiceRegistry) -> None:
        QObject.__init__(self)
        self._root = root
        self._services = registry
        self._scan_service = "stale-name-should-not-be-used"
        self._people_service = "stale-name-should-not-be-used"
        self._live_scan_buffer = {}
        self._current_scanner_worker = None
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

    # Host API used by the mixin
    def root(self) -> Path:
        return self._root

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

        def __init__(self, library_root, parent=None, *, people_service=None):
            super().__init__()
            recorded["people_service"] = people_service

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
