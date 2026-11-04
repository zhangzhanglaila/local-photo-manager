"""OCR engine using RapidOCR."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from ..config import DEFAULT_MODEL_CACHE_DIR, OCR_CONFIDENCE_THRESHOLD
from .models import OCRRegion

_LOGGER = logging.getLogger(__name__)


class OCREngine:
    """RapidOCR wrapper with lazy model loading.

    The engine loads models on first use (~10s) and keeps them in memory
    for subsequent calls. Thread-safe via a lock.
    """

    def __init__(self, model_cache_dir: Path | None = None) -> None:
        self._cache_dir = model_cache_dir or DEFAULT_MODEL_CACHE_DIR
        self._engine = None
        self._lock = threading.Lock()
        self._loaded = False

    def _ensure_loaded(self) -> bool:
        """Load RapidOCR models if not already loaded."""
        if self._loaded:
            return True

        with self._lock:
            if self._loaded:
                return True

            try:
                from rapidocr_onnxruntime import RapidOCR

                # RapidOCR builds its own onnxruntime sessions without a thread
                # limit, which defaults to one thread per core and starved the
                # interface while an OCR scan ran in the background.
                from ..onnx_threads import bound_rapidocr_threads

                bound_rapidocr_threads()

                _LOGGER.info("Loading RapidOCR model (first use, ~10s)...")
                self._engine = RapidOCR()
                self._loaded = True
                _LOGGER.info("RapidOCR model loaded successfully")
                return True

            except ImportError:
                _LOGGER.warning(
                    "rapidocr-onnxruntime not installed. "
                    "Install with: pip install 'iPhoto[ai]'"
                )
                return False
            except Exception as e:
                _LOGGER.error("Failed to load RapidOCR model: %s", e)
                return False

    def extract_text(self, image_path: Path) -> list[OCRRegion]:
        """Extract all text regions from an image.

        Parameters
        ----------
        image_path : Path
            Path to the image file.

        Returns
        -------
        list[OCRRegion]
            List of detected text regions, filtered by confidence threshold.
        """
        if not self._ensure_loaded():
            return []

        try:
            result, _ = self._engine(str(image_path))
            if result is None:
                return []

            regions: list[OCRRegion] = []
            for box, text, confidence in result:
                # Filter by confidence (rapidocr may return str)
                confidence = float(confidence)
                if confidence < OCR_CONFIDENCE_THRESHOLD:
                    continue

                # box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                # Convert to bounding rect
                x_min = min(p[0] for p in box)
                y_min = min(p[1] for p in box)
                x_max = max(p[0] for p in box)
                y_max = max(p[1] for p in box)

                regions.append(OCRRegion(
                    text=text.strip(),
                    confidence=float(confidence),
                    box_x=float(x_min),
                    box_y=float(y_min),
                    box_w=float(x_max - x_min),
                    box_h=float(y_max - y_min),
                ))

            return regions

        except Exception as e:
            _LOGGER.warning("OCR failed for %s: %s", image_path, e)
            return []

    def is_loaded(self) -> bool:
        """Check if the model is loaded."""
        return self._loaded

    def preload(self) -> bool:
        """Preload the model. Returns True if successful."""
        return self._ensure_loaded()
