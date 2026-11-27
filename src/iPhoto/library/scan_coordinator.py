"""Scan scheduling, progress tracking, and live scan compatibility reads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from PySide6.QtCore import QMutexLocker, QRunnable, QThread

from ..infrastructure.services.library_scan_service import LibraryScanService
from ..utils.logging import get_logger
from .workers.face_scan_worker import FaceScanWorker
from .workers.scanner_worker import ScannerSignals, ScannerWorker
from ..utils.pathutils import paths_equal
from ..application.dtos import ScanStatus
from ..i18n import tr

if TYPE_CHECKING:
    pass

LOGGER = get_logger()


class _PairingWorker(QRunnable):
    """Run live-photo pairing off the main thread after a scan completes."""

    def __init__(
        self,
        scan_root: Path,
        scan_service: LibraryScanService | None = None,
    ) -> None:
        super().__init__()
        self._scan_root = scan_root
        self._scan_service = scan_service

    def run(self) -> None:
        try:
            scan_service = self._scan_service
            if scan_service is None:
                LOGGER.warning(
                    "Skipping live photo pairing for %s because no bound scan service is available",
                    self._scan_root,
                )
                return
            scan_service.pair_album(self._scan_root)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Failed to persist live photo pairings after scan of %s "
                "(re-scanning the library will retry pairing): %s",
                self._scan_root,
                exc,
            )


class ScanCoordinatorMixin:
    """Mixin providing scan scheduling and progress for LibraryRuntimeController."""

    def _ai_face_enabled(self) -> bool:
        """Whether the face scan is switched on in 设置 → 智能."""

        switches = getattr(self, "_feature_switches", None)
        return True if switches is None else switches.face_enabled()

    def _ai_ocr_enabled(self) -> bool:
        """Whether the text-in-photos scan is switched on."""

        switches = getattr(self, "_feature_switches", None)
        return True if switches is None else switches.ocr_enabled()

    def _ai_model_selection(self):
        """Return the injected model selection, or a standalone default."""

        selection = getattr(self, "_model_selection", None)
        if selection is None:
            from ..agent.services.model_selection import ModelSelection

            selection = ModelSelection()
        return selection

    @staticmethod
    def _face_worker_has_finished(worker) -> bool:
        """Whether a ``QThread``-based face worker has already returned."""

        checker = getattr(worker, "isFinished", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except RuntimeError:
            return True

    @staticmethod
    def _reopen_ai_input(worker) -> None:
        """Let an existing worker accept rows from a follow-up root scan.

        Sequential scans reuse the same worker. Between two roots it must not
        treat the queue as drained and exit before the next root's chunks arrive.
        """

        resume = getattr(worker, "resume_input", None)
        if callable(resume):
            resume()
            return
        # Compatibility for small test doubles and older workers.
        if hasattr(worker, "_input_closed"):
            worker._input_closed = False
        if hasattr(worker, "_finished"):
            worker._finished = False

    def _cancel_face_scan(self) -> None:
        """Cancel the face worker and remove its task row immediately."""

        worker = self._current_face_scanner
        self._current_face_scanner = None
        self._face_scan_pending_start = False
        self._face_scan_started = False
        self._face_scan_status_message = None
        if worker is None:
            return
        try:
            worker.cancel()
        except RuntimeError:  # pragma: no cover - Qt object already deleted
            LOGGER.debug("Failed to cancel face worker", exc_info=True)
        self._emit_ai_cancelled("face")
        self.faceScanStatusChanged.emit("")

    def _cancel_ocr_scan(self) -> None:
        """Cancel the OCR worker and remove its task row immediately."""

        worker = self._current_ocr_scanner
        self._current_ocr_scanner = None
        self._ocr_scan_pending_start = False
        self._ocr_scan_started = False
        if worker is None:
            return
        try:
            worker.cancel()
        except RuntimeError:  # pragma: no cover - Qt object already deleted
            LOGGER.debug("Failed to cancel OCR worker", exc_info=True)
        self._emit_ai_cancelled("ocr")
        self.ocrScanStatusChanged.emit(ScanStatus(""))

    def start_session_scan(
        self,
        root: Path,
        *,
        include: Iterable[str],
        exclude: Iterable[str],
    ) -> None:
        """Start a scan through the session-facing runtime controller surface."""

        self.start_scanning(root, include, exclude)

    def start_scanning(self, root: Path, include: Iterable[str], exclude: Iterable[str]) -> None:
        """Start a background scan for the given root directory.

        All scanned assets are written to the global database at the library root.

        Face and OCR scanning are deferred until the file scan completes to avoid
        resource contention (CPU, memory, disk I/O) during the initial filesystem
        walk.  Rows are queued into the workers during the scan so they are ready
        to process once the file scan finishes.
        """
        # Prepare signals outside the lock. The slots check which scanner sent
        # the event: cancelling an old scan does not stop its queued signals, and
        # letting a stale ``finished`` finalize the *new* scan started the OCR
        # worker with an empty queue (it then reported "complete" after 0 rows).
        signals = ScannerSignals()
        signals.progressUpdated.connect(self._on_scanner_progress)
        signals.chunkReady.connect(self._on_scan_chunk)
        signals.finished.connect(self._on_scan_finished)
        signals.error.connect(self._on_scan_error)
        signals.batchFailed.connect(self._on_scan_batch_failed)

        # Check if already scanning the same root (thread-safe)
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not None:
            if self._live_scan_root and self._paths_equal(self._live_scan_root, root):
                return
            # Cancel the old scan before starting new one (inline to avoid deadlock)
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            self._live_scan_root = None
        # Keep the AI workers that are already collecting rows for this library.
        # Startup scans the primary root and then each extra root in sequence;
        # recreating the workers on every step discarded the rows queued by the
        # previous scan and left the old task rows "waiting" forever because the
        # replacement worker made their finish/cancel signals look stale.
        self._scan_generation = getattr(self, "_scan_generation", 0) + 1
        signals._generation = self._scan_generation
        self._live_scan_root = root
        self._live_scan_buffer.clear()

        # Pass library root to scanner so all assets go to global database
        worker = ScannerWorker(
            root,
            include,
            exclude,
            signals,
            library_root=self.root(),
            scan_service=self._services.scan_service,
        )
        # ``signals`` is per worker; the slots read this tag back through
        # ``self.sender()`` to drop queued events from a superseded scan.
        signals._worker = worker
        self._current_scanner_worker = worker
        face_library_root = self.root() if self.root() is not None else root

        # Skip face scan when disk space is critically low to avoid SQLite crashes,
        # or when the user has disabled automatic face scanning via env var.
        import os as _os
        import shutil as _shutil
        _skip_face = False
        try:
            _du = _shutil.disk_usage(str(face_library_root))
            _skip_face = _du.free < 500 * 1024 * 1024  # 500 MB
        except OSError:
            pass

        # Allow disabling auto face scan via IPHOTO_SKIP_FACE_SCAN=1 env var.
        if not _skip_face and _os.environ.get("IPHOTO_SKIP_FACE_SCAN", "").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            _skip_face = True
            self.faceScanStatusChanged.emit(tr("face.status.disabled_env"))

        # The settings switch (设置 → 智能) does the same thing without an env var.
        if not _skip_face and not self._ai_face_enabled():
            _skip_face = True
            self.faceScanStatusChanged.emit(tr("face.status.disabled_setting"))

        if _skip_face:
            self._cancel_face_scan()
            self.faceScanStatusChanged.emit(
                tr("face.status.skipped")
                if not self._face_scan_status_message
                else ""
            )
        else:
            face_worker = self._current_face_scanner
            if face_worker is not None and self._face_worker_has_finished(face_worker):
                # The thread returned but its queued ``finished`` signal has not
                # reached the GUI thread yet. Reap it now so the next scan gets a
                # worker that can actually be started again.
                self._on_face_scan_finished(face_worker)
                face_worker = None
            if face_worker is None:
                # Create the face worker but DO NOT start its thread yet. Rows
                # will be queued via enqueue_rows() during the file scan, and
                # the thread starts once the whole scan sequence is done.
                face_worker = FaceScanWorker(
                    face_library_root,
                    self,
                    people_service=self._services.people_service,
                    model_pack=self._ai_model_selection().spec("face").dir_name,
                )
                face_worker.statusChanged.connect(
                    lambda message, worker=face_worker: self._on_face_scan_status_changed(
                        message, worker
                    )
                )
                face_worker.downloadProgress.connect(
                    lambda downloaded, total, worker=face_worker: self._on_face_download_progress(
                        downloaded, total, worker
                    )
                )
                face_worker.finished.connect(
                    lambda worker=face_worker: self._on_face_scan_finished(worker)
                )
                face_progress = getattr(face_worker, "progressChanged", None)
                if face_progress is not None:
                    face_progress.connect(
                        lambda done, total, worker=face_worker: self._on_face_progress(
                            done, total, worker
                        )
                    )
                self._current_face_scanner = face_worker
                self._face_scan_pending_start = True
                self._face_scan_started = False
                self._face_scan_status_message = None
                self.faceScanStatusChanged.emit("")
            else:
                # A later root in the same startup sequence reuses the worker so
                # the rows from all roots stay in one queue.
                self._reopen_ai_input(face_worker)
                self._face_scan_pending_start = not bool(
                    getattr(self, "_face_scan_started", False)
                )

        # Same lifecycle for OCR: reuse the worker while this scan sequence is
        # still collecting rows, and only start it after the final root.
        ocr_library_root = self.root() if self.root() is not None else root
        if not self._ai_ocr_enabled():
            self._cancel_ocr_scan()
        ocr_worker = self._current_ocr_scanner
        if ocr_worker is None and self._ai_ocr_enabled():
            ocr_worker = self._create_ocr_worker(ocr_library_root)
            if ocr_worker is not None:
                ocr_worker.signals.progress.connect(
                    lambda done, total, worker=ocr_worker: self._on_ocr_progress(
                        done, total, worker
                    )
                )
                ocr_worker.signals.finished.connect(
                    lambda processed, failed, worker=ocr_worker: self._on_ocr_finished(
                        processed, failed, worker
                    )
                )
                ocr_worker.signals.error.connect(
                    lambda message, worker=ocr_worker: self._on_ocr_error(
                        message, worker
                    )
                )
                self._current_ocr_scanner = ocr_worker
                self._ocr_scan_pending_start = True
                self._ocr_scan_started = False
            else:
                self._ocr_scan_pending_start = False
        elif ocr_worker is not None:
            self._reopen_ai_input(ocr_worker)
            self._ocr_scan_pending_start = not bool(
                getattr(self, "_ocr_scan_started", False)
            )

        # Release lock before starting the worker
        del locker

        # Only start the file scanner immediately.  Face and OCR workers will
        # be started in _on_scan_finished after the filesystem walk completes.
        self._scan_thread_pool.start(worker)

    def stop_scanning(self) -> None:
        """Cancel the currently running scan, if any."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker:
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            # We don't clear the buffer immediately on stop, as the UI might still need it
            # until a new scan starts or the app closes. Setting root to None invalidates it contextually.
            self._live_scan_root = None
        self._cancel_face_scan()
        self._cancel_ocr_scan()
        self._face_scan_pending_start = False
        self._ocr_scan_pending_start = False

    def is_scanning_path(self, path: Path) -> bool:
        """Return True if the given path is covered by the active scan."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if not self._live_scan_root:
            return False

        try:
            target = path.resolve()
            scan_root = self._live_scan_root.resolve()
            if target == scan_root:
                return True
            # Check if target is a subdirectory of scan_root
            return scan_root in target.parents
        except (OSError, ValueError):
            return False

    def get_live_scan_results(self, relative_to: Optional[Path] = None) -> List[Dict]:
        """Return a best-effort snapshot of live scan results.

        Args:
            relative_to: If provided, only returns items that are descendants of
                this path.

        Notes:
            The scan coordinator no longer treats the in-memory list as the
            authoritative browsing cache. When available, results are now read
            back from the on-disk database snapshot. The old in-memory list is
            only used as a compatibility fallback for tests or callers that
            manually inject rows.
        """
        locker = QMutexLocker(self._scan_buffer_lock)
        scan_root = self._live_scan_root
        buffer_snapshot = list(self._live_scan_buffer)
        library_root = self.root()
        # Release the lock before performing any potentially slow I/O.
        del locker

        if scan_root is None:
            return []

        if buffer_snapshot:
            return self._remap_live_rows(buffer_snapshot, scan_root, relative_to)

        if library_root is None:
            return []

        base_root = scan_root if relative_to is None else relative_to
        query_root = self._resolve_live_query_root(scan_root, base_root)
        if query_root is None:
            return []

        db_rows = self._read_live_rows_from_query_service(query_root, library_root)
        if not db_rows:
            return []
        return self._rewrite_rows_relative_to(db_rows, query_root, base_root)

    def _resolve_live_query_root(
        self,
        scan_root: Path,
        relative_to: Optional[Path],
    ) -> Optional[Path]:
        if relative_to is None:
            return scan_root
        try:
            rel_root_res = relative_to.resolve()
            scan_root_res = scan_root.resolve()
        except OSError:
            return None

        if scan_root_res == rel_root_res:
            return scan_root
        if rel_root_res in scan_root_res.parents:
            return scan_root
        if scan_root_res in rel_root_res.parents:
            return relative_to
        return None

    def _read_live_rows_from_query_service(
        self,
        query_root: Path,
        library_root: Path,
    ) -> List[Dict]:
        try:
            query_service = getattr(self, "asset_query_service", None)
            if query_service is None:
                return []
            rows = query_service.read_library_relative_asset_rows(
                query_root,
                sort_by_date=True,
                filter_hidden=True,
            )
            return [dict(row) for row in rows]
        except Exception:
            LOGGER.debug("Failed to read live scan rows from query service", exc_info=True)
            return []

    def _rewrite_rows_relative_to(
        self,
        rows: List[Dict],
        query_root: Path,
        relative_to: Optional[Path],
    ) -> List[Dict]:
        if self.root() is None:
            return []
        target_root = relative_to or query_root
        rewritten: List[Dict] = []
        for row in rows:
            rel_value = row.get("rel")
            if not isinstance(rel_value, str) or not rel_value:
                continue
            try:
                abs_path = (self.root() / rel_value).resolve()
                rel_path = abs_path.relative_to(target_root.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            updated = dict(row)
            updated["rel"] = rel_path
            rewritten.append(updated)
        return rewritten

    def _remap_live_rows(
        self,
        rows: List[Dict],
        scan_root: Path,
        relative_to: Optional[Path],
    ) -> List[Dict]:
        if relative_to is None:
            return list(rows)

        try:
            scan_root_res = scan_root.resolve()
            rel_root_res = relative_to.resolve()
        except OSError:
            return []

        if scan_root_res == rel_root_res:
            return list(rows)

        filtered: List[Dict] = []
        if rel_root_res in scan_root_res.parents:
            prefix = scan_root_res.relative_to(rel_root_res).as_posix()
            for item in rows:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str) or not item_rel:
                    continue
                new_item = item.copy()
                new_item["rel"] = f"{prefix}/{item_rel}"
                filtered.append(new_item)
            return filtered

        if scan_root_res in rel_root_res.parents:
            prefix = rel_root_res.relative_to(scan_root_res).as_posix()
            prefix_slash = f"{prefix}/"
            for item in rows:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str):
                    continue
                if item_rel == prefix or item_rel.startswith(prefix_slash):
                    new_item = item.copy()
                    new_item["rel"] = item_rel[len(prefix_slash):] if item_rel != prefix else ""
                    if new_item["rel"]:
                        filtered.append(new_item)
            return filtered

        return []

    def _sender_scan_worker(self):
        """Return the scanner that emitted the event being handled, if any."""

        return getattr(self.sender(), "_worker", None)

    def _scan_event_is_stale(self) -> bool:
        sender = self.sender()
        generation = getattr(sender, "_generation", None)
        if generation is not None and generation != getattr(
            self, "_scan_generation", generation
        ):
            return True
        worker = self._sender_scan_worker()
        return worker is not None and worker is not self._current_scanner_worker

    def _on_scanner_progress(self, root: Path, current: int, total: int) -> None:
        if self._scan_event_is_stale():
            return
        self.scanProgress.emit(root, current, total)

    def _on_scan_chunk(self, root: Path, chunk: List[dict]) -> None:
        """Handle incoming scan chunks after persistence.

        A cancelled scan can still have ``chunkReady`` events queued; enqueueing
        them into the new scan's AI workers mixed two libraries' rows. The scan
        worker already persisted each chunk before this signal is observed, so
        the browsing layer only needs the invalidation event.
        """

        if self._scan_event_is_stale():
            return

        if not chunk:
            return

        self.invalidate_geotagged_assets_cache()
        if self._current_face_scanner is not None:
            self._current_face_scanner.enqueue_rows(chunk)
        if self._current_ocr_scanner is not None:
            self._current_ocr_scanner.enqueue_rows(chunk)
        self.scanChunkReady.emit(root, chunk)

    def _on_scan_finished(self, root: Path, rows: List[dict]) -> None:
        if self._scan_event_is_stale():
            return
        self.invalidate_geotagged_assets_cache()
        generation = getattr(self, "_scan_generation", None)

        # Clear worker reference before downstream listeners react so a completed
        # scan does not still appear in-flight while final post-processing runs.
        locker = QMutexLocker(self._scan_buffer_lock)
        worker = self._current_scanner_worker
        self._current_scanner_worker = None
        self._live_scan_root = None
        face_scanner = self._current_face_scanner
        ocr_scanner = self._current_ocr_scanner
        del locker

        if worker is None:
            if self._live_scan_root is None:
                self.scanFinished.emit(root, True)
            return

        if worker.cancelled:
            self.scanFinished.emit(root, True)
            return
        if worker.failed:
            self.scanFinished.emit(root, False)
            return

        # Persist Live Photo pairings once a scan completes so the database and
        # links.json reflect the latest scan results.
        scan_service = worker.scan_service
        try:
            scan_service.finalize_scan_result(root, rows, pair_live=False)
        except Exception as exc:
            LOGGER.warning("Failed to persist scan finalization for %s: %s", root, exc)

        # Emit immediately so the UI (status bar, map refresh) can react without
        # waiting for the potentially slow live-photo pairing step.
        self.scanFinished.emit(root, True)

        # Persist live-photo pairings in the background to avoid blocking the
        # main thread while downstream listeners start refreshing.
        self._scan_thread_pool.start(_PairingWorker(root, scan_service))

        # Load the semantic-search model before the face scan starts. It used to be
        # loaded lazily on the first search, so the user waited ~10 s staring at a
        # progress panel; loading it here means search is ready by the time the scan
        # finishes, and the (CPU heavy) face scan runs afterwards.
        self._preload_search_model()

        # ``scanFinished`` is delivered synchronously. A listener can start the
        # next root's scan (startup scans roots sequentially) or a watcher-driven
        # rescan before this method resumes. That newer scan owns the AI input
        # now; closing it here would make the reused workers drain before the new
        # root's chunks arrive. The newer scan will close the input when it ends.
        if generation is not None and generation != getattr(
            self, "_scan_generation", generation
        ):
            LOGGER.debug(
                "scan %s finished after a newer scan started; leaving AI input open",
                root,
            )
            return
        if self._current_scanner_worker is not None or self._live_scan_root is not None:
            return

        # --- Start deferred AI workers after the whole scan sequence ---
        # Face and OCR scanning were deferred to avoid resource contention with
        # the filesystem walk. All roots in a startup sequence feed the same
        # workers, so only the final root closes their input and starts them.
        if face_scanner is not None:
            face_scanner.finish_input()
        if ocr_scanner is not None:
            ocr_scanner.finish_input()

        if (
            face_scanner is not None
            and getattr(self, "_face_scan_pending_start", False)
        ):
            self._face_scan_pending_start = False
            self._face_scan_started = True
            self._emit_ai_started("face")
            face_scanner.start(QThread.Priority.LowestPriority)
            self.faceScanStatusChanged.emit(tr("face.status.scanning"))

        if (
            ocr_scanner is not None
            and getattr(self, "_ocr_scan_pending_start", False)
        ):
            self._ocr_scan_pending_start = False
            self._ocr_scan_started = True
            queued = len(getattr(ocr_scanner, "_queue", []) or [])
            LOGGER.info("starting OCR with %d queued images", queued)
            self._emit_ai_started("ocr")
            self.ocrScanStatusChanged.emit(ScanStatus("status.ocr.running"))
            self._scan_thread_pool.start(ocr_scanner)

    def _preload_search_model(self) -> None:
        """Warm the semantic-search model in the background, once per session.

        Failures are logged and ignored: search keeps its own lazy load as a fallback.
        """

        if getattr(self, "_search_model_preload_started", False):
            return
        self._search_model_preload_started = True
        session = getattr(self, "_library_session", None)
        if session is None:
            return

        def _load() -> None:
            try:
                # Only warm an already downloaded model: `get_embedding_service`
                # opens a download dialog when files are missing, and that must not
                # happen on a background thread.
                from iPhoto.agent.services import search_models

                if not search_models.is_installed(
                    self._ai_model_selection().spec("search")
                ):
                    LOGGER.info("CLIP model not downloaded; skipping preload")
                    return

                service = session.get_embedding_service()
                loader = getattr(service, "load", None) if service is not None else None
                if callable(loader):
                    # `is_loaded()` only reports state; `load()` actually warms it.
                    if loader():
                        LOGGER.info("Semantic search model preloaded")
            except Exception:
                LOGGER.debug("Semantic search model preload failed", exc_info=True)

        import threading

        threading.Thread(
            target=_load, name="search-model-preload", daemon=True
        ).start()

    def _on_scan_error(self, root: Path, message: str) -> None:
        if self._scan_event_is_stale():
            return
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None
        self._live_scan_root = None
        face_scanner = self._current_face_scanner
        ocr_scanner = self._current_ocr_scanner
        del locker
        # Keep the workers (and the rows already queued) for a later rescan;
        # only close their input so a running worker can drain.
        if face_scanner is not None:
            face_scanner.finish_input()
        if ocr_scanner is not None:
            ocr_scanner.finish_input()
        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)

    def _on_scan_batch_failed(self, root: Path, count: int) -> None:
        """Propagate partial failure notifications to the UI."""
        if self._scan_event_is_stale():
            return
        self.scanBatchFailed.emit(root, count)

    def _paths_equal(self,p1,p2) -> bool:
        """Delegate to the single shared comparison."""

        return paths_equal(p1,p2)

    # ------------------------------------------------------------------
    # AI-scan task lifecycle (machine-readable)
    # ------------------------------------------------------------------
    def _emit_ai_started(self, feature: str) -> None:
        signal = getattr(self, "aiScanStarted", None)
        if signal is not None:
            signal.emit(feature)

    def _emit_ai_progress(self, feature: str, done: int, total: int) -> None:
        signal = getattr(self, "aiScanProgress", None)
        if signal is not None:
            signal.emit(feature, max(0, int(done)), max(0, int(total)))

    def _emit_ai_finished(self, feature: str, failed: bool) -> None:
        signal = getattr(self, "aiScanFinished", None)
        if signal is not None:
            signal.emit(feature, bool(failed))

    def _emit_ai_cancelled(self, feature: str) -> None:
        signal = getattr(self, "aiScanCancelled", None)
        if signal is not None:
            signal.emit(feature)

    def _on_face_progress(self, done: int, total: int, worker=None) -> None:
        if worker is not None and worker is not self._current_face_scanner:
            return
        self._emit_ai_progress("face", done, total)

    def _on_face_scan_status_changed(self, message: str, worker=None) -> None:
        if worker is not None and worker is not self._current_face_scanner:
            return
        self._face_scan_status_message = message or None
        self.faceScanStatusChanged.emit(message)

    def _on_face_download_progress(
        self, downloaded: int, total: int, worker=None
    ) -> None:
        if worker is not None and worker is not self._current_face_scanner:
            return
        if total > 0:
            pct = downloaded * 100 // total
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            msg = f"正在下载人脸检测模型... {pct}% ({mb_done:.1f}/{mb_total:.1f} MB)"
        else:
            mb_done = downloaded / (1024 * 1024)
            msg = f"正在下载人脸检测模型... {mb_done:.1f} MB"
        self._face_scan_status_message = msg
        self.faceScanStatusChanged.emit(msg)

    def _on_face_scan_finished(self, worker=None) -> None:
        current = self._current_face_scanner
        if worker is not None and current is not None and worker is not current:
            # A cancelled scan finished after a newer one was installed. It
            # must not clear the new worker's task or announce completion, but
            # still wait: dropping the last reference to a QThread that is
            # tearing down aborts the process.
            try:
                worker.wait()
            except RuntimeError:
                pass
            return
        if worker is None:
            worker = current
        self._current_face_scanner = None
        self._face_scan_pending_start = False
        self._face_scan_started = False
        if worker is not None:
            # ``finished`` is delivered on the GUI thread while the worker object
            # may still be tearing down. Dropping the last reference then let Qt
            # destroy a running QThread, which aborts the process with
            # "QThread: Destroyed while thread is still running". Waiting here is
            # immediate (``run`` has returned) and makes the hand-off safe.
            try:
                worker.wait()
            except RuntimeError:
                pass
        if bool(getattr(worker, "cancelled", False)):
            # Cancellation is not completion: drop the task row and clear the
            # transient "scanning faces" line instead of announcing "完成".
            self._emit_ai_cancelled("face")
            self.faceScanStatusChanged.emit("")
        else:
            self._emit_ai_finished("face", False)
            self.faceScanStatusChanged.emit(tr("face.status.complete"))

    # ------------------------------------------------------------------
    # Foreground media integration
    # ------------------------------------------------------------------
    def set_video_playing(self, is_playing: bool) -> None:
        """Step aside while the user is watching or viewing media.

        Both AI scans run on onnxruntime and read full-size images, so leaving
        them running while a photo or video is on screen is what made viewing
        feel sluggish. The name is historical: photo viewing uses the same path.
        """

        face_scanner = self._current_face_scanner
        if face_scanner is not None and face_scanner.isRunning():
            face_scanner.set_video_playing(is_playing)

        ocr_scanner = self._current_ocr_scanner
        if ocr_scanner is not None and hasattr(ocr_scanner, "set_paused"):
            ocr_scanner.set_paused(is_playing)

    # ------------------------------------------------------------------
    # OCR integration
    # ------------------------------------------------------------------

    def _create_ocr_worker(self, library_root: Path):
        """Create an OCR worker if AI dependencies are available."""
        try:
            from ..ai.ocr.ocr_engine import OCREngine
            from ..ai.db.ocr_database import get_ocr_repository
            from ..ai.workers.ocr_worker import OCRWorker

            if not self._ai_ocr_enabled():
                LOGGER.debug("OCR disabled in settings, skipping OCR scan")
                return None
            ocr_repo = get_ocr_repository(library_root)
            ocr_engine = OCREngine(spec=self._ai_model_selection().spec("ocr"))
            return OCRWorker(ocr_engine, ocr_repo, library_root)
        except ImportError:
            LOGGER.debug("OCR dependencies not available, skipping OCR scan")
            return None
        except Exception as exc:
            LOGGER.warning("Failed to create OCR worker: %s", exc)
            return None

    def _on_ocr_progress(self, done: int, total: int, worker=None) -> None:
        if worker is not None and worker is not self._current_ocr_scanner:
            return
        self._emit_ai_progress("ocr", done, total)
        if not done:
            return
        if total > 0 and (done % 25 == 0 or done >= total):
            self.ocrScanStatusChanged.emit(
                ScanStatus(
                    "status.ocr.progress_total",
                    {
                        "percent": min(100, done * 100 // total),
                        "done": done,
                        "total": total,
                    },
                )
            )
        elif total <= 0 and done % 50 == 0:
            self.ocrScanStatusChanged.emit(
                ScanStatus("status.ocr.progress", {"count": done})
            )

    def _on_ocr_error(self, message: str, worker=None) -> None:
        current = self._current_ocr_scanner
        if worker is not None and current is not None and worker is not current:
            return
        if worker is None or worker is current:
            self._current_ocr_scanner = None
            self._ocr_scan_pending_start = False
            self._ocr_scan_started = False
        if bool(getattr(worker, "cancelled", False)):
            self._emit_ai_cancelled("ocr")
            self.ocrScanStatusChanged.emit(ScanStatus(""))
            return
        self._emit_ai_finished("ocr", True)
        self.ocrScanStatusChanged.emit(
            ScanStatus("status.ocr.error", {"message": message}, completed=True)
        )

    def _on_ocr_finished(self, processed: int, failed: int, worker=None) -> None:
        current = self._current_ocr_scanner
        if worker is not None and current is not None and worker is not current:
            # The old scan's worker must not report "complete" over the new one.
            return
        if worker is None or worker is current:
            self._current_ocr_scanner = None
            self._ocr_scan_pending_start = False
            self._ocr_scan_started = False
        cancelled = bool(getattr(worker, "cancelled", False))
        if cancelled:
            LOGGER.info(
                "OCR worker cancelled after %d images (%d failed)", processed, failed
            )
            self._emit_ai_cancelled("ocr")
            self.ocrScanStatusChanged.emit(ScanStatus(""))
            return
        self._emit_ai_finished("ocr", bool(failed and not processed))
        if processed > 0:
            LOGGER.info("OCR scan complete: %d images processed, %d failed", processed, failed)
            self.ocrScanStatusChanged.emit(
                ScanStatus(
                    "status.ocr.done",
                    {"count": processed},
                    completed=True,
                )
            )
        else:
            self.ocrScanStatusChanged.emit(ScanStatus(""))
