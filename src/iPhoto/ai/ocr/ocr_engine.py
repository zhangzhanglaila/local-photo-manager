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

    def __init__(self, model_cache_dir: Path | None = None, spec=None) -> None:
        self._cache_dir = model_cache_dir or DEFAULT_MODEL_CACHE_DIR
        self._engine = None
        self._lock = threading.Lock()
        self._loaded = False
        #: Selected OCR pack; ``None`` means "use the bundled PP-OCRv3 models".
        self._spec = spec

    def _engine_params(self) -> dict:
        """Return RapidOCR 3.x parameters for the selected pack.

        The built-in pack asks for PP-OCRv5 *mobile*; RapidOCR downloads the ONNX
        files into ``extension/models/rapidocr`` the first time and reuses them
        afterwards. A pack found on disk is passed as explicit model paths.
        """

        from ...agent.services import ocr_models

        spec = self._spec or ocr_models.active_model()
        from ..onnx_threads import rapidocr_engine_params

        params: dict = {
            "Global.model_root_dir": str(ocr_models.rapidocr_dir()),
            **rapidocr_engine_params(),
        }
        if not spec.builtin:
            if spec.det is not None:
                params["Det.model_path"] = str(spec.det)
            if spec.rec is not None:
                params["Rec.model_path"] = str(spec.rec)
            if spec.cls is not None:
                params["Cls.model_path"] = str(spec.cls)
            return params

        try:
            from rapidocr.utils.typings import ModelType, OCRVersion

            version = OCRVersion(spec.version or ocr_models.BUILTIN_VERSION)
            model_type = ModelType(spec.model_type or ocr_models.BUILTIN_MODEL_TYPE)
        except Exception:  # pragma: no cover - enum names changed upstream
            _LOGGER.warning("RapidOCR enums unavailable; using its defaults")
            return params
        for task in ("Det", "Rec"):
            params[f"{task}.ocr_version"] = version
            params[f"{task}.model_type"] = model_type
        return params

    def _ensure_loaded(self) -> bool:
        """Load RapidOCR models if not already loaded."""
        if self._loaded:
            return True

        with self._lock:
            if self._loaded:
                return True

            try:
                from rapidocr import RapidOCR

                # Thread limits travel through ``_engine_params`` now; the 3.x
                # package exposes them explicitly.
                # RapidOCR also goes through OpenCV; cap its pool with the same
                # budget so a scan cannot saturate every core.
                from ...utils.thread_limits import apply_library_thread_limits

                apply_library_thread_limits()

                _LOGGER.info("Loading RapidOCR (PP-OCRv5 mobile) ...")
                params = self._engine_params()
                _LOGGER.info("OCR model pack: %s", params)
                self._engine = RapidOCR(params=params)
                self._loaded = True
                _LOGGER.info("RapidOCR model loaded successfully")
                return True

            except ImportError:
                _LOGGER.warning(
                    "rapidocr not installed. Install with: pip install 'iPhoto[ai]'"
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
            output = self._engine(str(image_path))
            if output is None or not getattr(output, "txts", None):
                return []

            regions: list[OCRRegion] = []
            for box, text, confidence in zip(
                output.boxes, output.txts, output.scores
            ):
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
