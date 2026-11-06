"""Fast CLIP embedding service using ONNX Runtime for CPU optimization."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)

_EMBEDDING_DIMENSION = 512


class CLIPFastEmbeddingService:
    """Fast CLIP embedding service using ONNX Runtime.

    Optimized for CPU inference with:
    - ONNX Runtime with graph optimization
    - Multi-threaded inference
    - Efficient image preprocessing
    """

    def __init__(self, model_dir: Path) -> None:
        """Initialize the fast CLIP embedding service."""
        self._model_dir = Path(model_dir)
        self._image_session = None
        self._text_session = None
        self._processor = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._loaded = False

        # ONNX model paths
        self._image_model_path = model_dir / "image_encoder.onnx"
        self._text_model_path = model_dir / "text_encoder.onnx"

    def _ensure_loaded(self) -> bool:
        """Load the ONNX models if not already loaded."""
        if self._loaded:
            return True

        with self._lock:
            if self._loaded:
                return True

            try:
                import onnxruntime as ort
                from transformers import CLIPProcessor, CLIPTokenizer

                _LOGGER.info("Loading CLIP ONNX models from %s", self._model_dir)

                # Check if ONNX models exist
                if not self._image_model_path.exists() or not self._text_model_path.exists():
                    _LOGGER.warning("ONNX models not found, falling back to PyTorch")
                    return False

                # Create ONNX Runtime sessions with optimizations
                opts = ort.SessionOptions()
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                opts.intra_op_num_threads = 4  # Use 4 threads
                opts.inter_op_num_threads = 2

                providers = ['CPUExecutionProvider']

                self._image_session = ort.InferenceSession(
                    str(self._image_model_path),
                    sess_options=opts,
                    providers=providers,
                )
                self._text_session = ort.InferenceSession(
                    str(self._text_model_path),
                    sess_options=opts,
                    providers=providers,
                )

                # Load processor and tokenizer
                self._processor = CLIPProcessor.from_pretrained(str(self._model_dir))
                self._tokenizer = CLIPTokenizer.from_pretrained(str(self._model_dir))

                self._loaded = True
                _LOGGER.info("CLIP ONNX models loaded successfully")
                return True

            except Exception as e:
                _LOGGER.error("Failed to load CLIP ONNX models: %s", e)
                return False

    def encode_image(self, image_path: Path) -> Optional[np.ndarray]:
        """Generate embedding for a single image."""
        if not self._ensure_loaded():
            return None

        try:
            from PIL import Image

            # Register HEIF/HEIC support
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass

            # Load and preprocess image
            image = Image.open(image_path).convert("RGB")
            inputs = self._processor(images=image, return_tensors="np")
            pixel_values = inputs["pixel_values"]

            # Run ONNX inference
            outputs = self._image_session.run(None, {"pixel_values": pixel_values})
            embedding = outputs[0][0]

            # Normalize
            embedding = embedding / np.linalg.norm(embedding)
            return embedding.astype(np.float32)

        except Exception as e:
            _LOGGER.warning("Failed to encode image %s: %s", image_path, e)
            return None

    def encode_images(self, image_paths: List[Path]) -> List[Optional[np.ndarray]]:
        """Generate embeddings for multiple images."""
        if not self._ensure_loaded():
            return [None] * len(image_paths)

        # Register HEIF/HEIC support once
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass

        results = []
        for path in image_paths:
            results.append(self.encode_image(path))
        return results

    def encode_text(self, text: str) -> Optional[np.ndarray]:
        """Generate embedding for text query."""
        if not self._ensure_loaded():
            return None

        try:
            # Tokenize text
            inputs = self._tokenizer(
                text,
                return_tensors="np",
                padding=True,
                truncation=True,
                max_length=77,
            )

            # Run ONNX inference
            outputs = self._text_session.run(
                None,
                {
                    "input_ids": inputs["input_ids"],
                    "attention_mask": inputs["attention_mask"],
                },
            )
            embedding = outputs[0][0]

            # Normalize
            embedding = embedding / np.linalg.norm(embedding)
            return embedding.astype(np.float32)

        except Exception as e:
            _LOGGER.warning("Failed to encode text '%s': %s", text, e)
            return None

    def compute_similarity(self, embedding1: np.ndarray, embedding2: np.ndarray) -> float:
        """Compute cosine similarity between two embeddings."""
        return float(np.dot(embedding1, embedding2))

    def load(self) -> bool:
        """Load the model now (public wrapper around the lazy load).

        `is_loaded` only reports state; callers that want to warm the model — for
        example the preload that runs before the face scan — need this.
        """

        return bool(self._ensure_loaded())

    def is_loaded(self) -> bool:
        """Check if the model is loaded."""
        return self._loaded

    def get_embedding_dimension(self) -> int:
        """Return the dimension of the embedding vectors."""
        return _EMBEDDING_DIMENSION
