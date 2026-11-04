"""Background worker for OCR text extraction."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, Signal, Slot

from ..config import OCR_BATCH_SIZE, OCR_IMAGE_EXTENSIONS

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
            rel = row.get("rel", "")
            if not rel:
                continue
            ext = Path(rel).suffix.lower()
            if ext in OCR_IMAGE_EXTENSIONS:
                filtered.append(row)

        self._queue.extend(filtered)

    @Slot()
    def finish_input(self) -> None:
        """Signal that no more chunks will arrive."""
        self._finished = True

    def cancel(self) -> None:
        """Cancel processing."""
        self._cancelled = True

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
                    continue

                image_path = self._library_root / asset_rel
                if not image_path.exists():
                    failed += 1
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

                self.signals.progress.emit(processed, processed + failed)

        self.signals.finished.emit(processed, failed)
        _LOGGER.info("OCR worker finished: %d processed, %d failed", processed, failed)
