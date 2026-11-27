"""Background worker for OCR text extraction."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, Signal, Slot

from ..config import OCR_BATCH_SIZE, OCR_IMAGE_EXTENSIONS
from iPhoto.ai.scan_gate import serialized_scan

_LOGGER = logging.getLogger(__name__)


class OCRWorkerSignals(QObject):
    """Signals for the OCR worker."""

    progress = Signal(int, int)  # (processed, total)
    chunk_ready = Signal(list)   # list[dict] - rows needing OCR
    finished = Signal(int, int)  # (processed, failed)
    error = Signal(str)


class OCRWorker(QRunnable):
    """Background worker for OCR text extraction.

    Receives scan chunks via ``enqueue_rows()``, filters for image files,
    and processes them through the OCR engine. Results are stored in the
    OCR repository.
    """

    def __init__(
        self,
        ocr_engine,
        ocr_repository,
        library_root: Path,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)

        self._engine = ocr_engine
        self._repo = ocr_repository
        self._library_root = library_root
        self._queue: list[dict] = []
        self._finished = False
        self._cancelled = False
        self._paused = False
        #: Rows accepted for this run. The worker is started after the file scan
        #: has closed its input, so this is the real denominator for progress;
        #: it also keeps counting if a live scan feeds more rows in while running.
        self._admitted_total = 0
        # Images already processed in an earlier session (including ones with no
        # text) so a restart does not redo hours of OCR.
        try:
            self._processed: set[str] = set(
                self._repo.get_processed_asset_ids()
            )
        except Exception:
            self._processed = set()

        self.signals = OCRWorkerSignals()

    def set_paused(self, paused: bool) -> None:
        """Pause or resume OCR processing.

        OCR runs through onnxruntime and reads full-size images, so it competes
        with whatever the user is looking at. When a photo is on screen the scan
        steps aside instead of slowing the interface down.
        """

        self._paused = bool(paused)

    @Slot(list)
    def enqueue_rows(self, rows: list[dict]) -> None:
        """Receive scan chunks, filter for image files."""
        if self._cancelled:
            return

        filtered = []
        for row in rows:
            asset_id = row.get("id", "")
            if asset_id and asset_id in self._processed:
                continue
            rel = row.get("rel", "")
            if not rel:
                continue
            ext = Path(rel).suffix.lower()
            if ext in OCR_IMAGE_EXTENSIONS:
                filtered.append(row)

        self._admitted_total += len(filtered)
        self._queue.extend(filtered)

    @Slot()
    def finish_input(self) -> None:
        """Signal that no more chunks will arrive."""
        self._finished = True

    def resume_input(self) -> None:
        """Reopen the queue while a follow-up root scan keeps feeding rows."""

        if not self._cancelled:
            self._finished = False

    def cancel(self) -> None:
        """Cancel processing."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Whether :meth:`cancel` was requested before the worker drained."""

        return self._cancelled

    @serialized_scan

    def run(self) -> None:
        """Process all queued images for OCR."""
        try:
            self._process_queue()
        except Exception as exc:
            _LOGGER.exception("OCR worker failed: %s", exc)
            self.signals.error.emit(str(exc))

    def _process_queue(self) -> None:
        """Process all queued images."""
        processed = 0
        failed = 0
        # This runs after the AI-scan gate is acquired, so the first progress
        # tick also tells the UI "this is the scan that is actually running".
        self._emit_progress(processed, failed)

        while not self._cancelled:
            if self._paused:
                QThread.msleep(200)
                continue

            # Wait for items if not finished
            if not self._queue:
                if self._finished:
                    break
                QThread.msleep(100)
                continue

            # Take a batch
            batch = self._queue[:OCR_BATCH_SIZE]
            self._queue = self._queue[OCR_BATCH_SIZE:]

            for row in batch:
                if self._cancelled:
                    break

                asset_id = row.get("id", "")
                asset_rel = row.get("rel", "")
                if not asset_id or not asset_rel:
                    # Count the attempt: otherwise progress never reaches the
                    # number of rows admitted to the queue.
                    failed += 1
                    self._emit_progress(processed, failed)
                    continue

                image_path = self._library_root / asset_rel
                if not image_path.exists():
                    failed += 1
                    self._emit_progress(processed, failed)
                    continue

                try:
                    regions = self._engine.extract_text(image_path)
                    if regions:
                        self._repo.store_regions(
                            asset_id=asset_id,
                            asset_rel=asset_rel,
                            regions=regions,
                            image_width=row.get("width", 0) or 0,
                            image_height=row.get("height", 0) or 0,
                        )
                    processed += 1
                except Exception as e:
                    _LOGGER.debug("OCR failed for %s: %s", asset_rel, e)
                    failed += 1

                self._mark_processed(asset_id, asset_rel)
                self._emit_progress(processed, failed)

        self._emit_progress(processed, failed)
        self.signals.finished.emit(processed, failed)
        _LOGGER.info("OCR worker finished: %d processed, %d failed", processed, failed)

    def _emit_progress(self, processed: int, failed: int) -> None:
        """Report ``(attempted, expected)`` for this run."""

        done = processed + failed
        total = max(self._admitted_total, done)
        self.signals.progress.emit(done, total)

    def _mark_processed(self, asset_id: str, asset_rel: str) -> None:
        """Remember the attempt so the next launch can skip this image."""

        try:
            self._repo.mark_processed(asset_id, asset_rel)
        except Exception:
            _LOGGER.debug(
                "Failed to record OCR progress for %s", asset_rel, exc_info=True
            )
        self._processed.add(asset_id)
