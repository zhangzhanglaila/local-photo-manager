"""CLIP embedding service using transformers."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)

# Importing ``transformers`` from two threads at the same time can observe its lazy
# module while it is still initialising, which surfaces as
# "cannot import name 'CLIPModel' from 'transformers'". Serialise the import and
# the weight load across service instances (searches and the startup preload each
# may hold their own instance).
_LOAD_LOCK = threading.Lock()

# CLIP model ID
_MODEL_ID = "openai/clip-vit-base-patch32"
_EMBEDDING_DIMENSION = 512

# Register HEIF/HEIC support once at module level
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


class CLIPEmbeddingService:
    """CLIP embedding service using transformers.

    This service loads CLIP model and provides methods for encoding
    images and text into embeddings.
    """

    def __init__(self, model_dir: Path) -> None:
        """Initialize the CLIP embedding service.

        Parameters
        ----------
        model_dir : Path
            Directory containing the model files.
        """
        self._model_dir = Path(model_dir)
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._loaded = False
        # Text vectors are deterministic; caching them keeps repeated searches and
        # the label vocabulary from re-encoding the same strings.
        self._text_cache: dict[str, np.ndarray] = {}

    def _ensure_loaded(self) -> bool:
        """Load the CLIP model if not already loaded."""
        if self._loaded:
            return True

        with self._lock:
            if self._loaded:
                return True

            with _LOAD_LOCK:
                try:
                    import torch
                    from transformers import CLIPModel, CLIPProcessor, CLIPTokenizer

                    _LOGGER.info("Loading CLIP model from %s", self._model_dir)

                    # Load model with optimizations
                    self._model = CLIPModel.from_pretrained(
                        str(self._model_dir),
                        torch_dtype=torch.float32,  # Use float32 for CPU
                    )
                    self._processor = CLIPProcessor.from_pretrained(str(self._model_dir))
                    self._tokenizer = CLIPTokenizer.from_pretrained(str(self._model_dir))

                    # Set to eval mode and optimize
                    self._model.eval()
                    if hasattr(torch, 'set_float32_matmul_precision'):
                        torch.set_float32_matmul_precision('medium')

                    self._loaded = True
                    _LOGGER.info("CLIP model loaded successfully")
                    return True

                except Exception as e:
                    _LOGGER.error("Failed to load CLIP model: %s", e)
                    return False

    def encode_image(self, image_path: Path) -> Optional[np.ndarray]:
        """Generate embedding for a single image."""
        if not self._ensure_loaded():
            return None

        try:
            import torch
            from PIL import Image

            # Load image
            image = Image.open(image_path).convert("RGB")

            # Process image
            inputs = self._processor(images=image, return_tensors="pt")

            # Generate embedding using vision model
            with torch.no_grad():
                outputs = self._model.vision_model(**inputs)
                image_embeds = outputs.pooler_output
                image_embeds = self._model.visual_projection(image_embeds)

            # Normalize
            embedding = image_embeds[0].numpy()
            embedding = embedding / np.linalg.norm(embedding)

            return embedding.astype(np.float32)

        except Exception as e:
            _LOGGER.warning("Failed to encode image %s: %s", image_path, e)
            return None

    def encode_images(
        self,
        image_paths: List[Path],
        *,
        batch_size: int = 16,
    ) -> List[Optional[np.ndarray]]:
        """Generate embeddings for multiple images in true batches.

        Each image used to be a separate forward pass, which made indexing a
        14k-photo library take hours. A batch of 16 costs barely more than one
        image on CPU, and this is the path the background indexer uses.

        A path that cannot be decoded yields ``None`` in its slot; it never
        aborts the rest of the batch.
        """

        if not self._ensure_loaded():
            return [None] * len(image_paths)
        if not image_paths:
            return []

        import torch
        from PIL import Image

        results: list[Optional[np.ndarray]] = [None] * len(image_paths)
        batch_size = max(1, int(batch_size))

        batch_images: list[object] = []
        batch_slots: list[int] = []

        def flush() -> None:
            if not batch_images:
                return
            try:
                inputs = self._processor(images=list(batch_images), return_tensors="pt")
                with torch.no_grad():
                    outputs = self._model.vision_model(**inputs)
                    embeds = self._model.visual_projection(outputs.pooler_output)
                array = embeds.numpy()
                norms = np.linalg.norm(array, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                array = array / norms
                for slot, vector in zip(batch_slots, array):
                    results[slot] = vector.astype(np.float32)
            except Exception as exc:  # pragma: no cover - model failure
                _LOGGER.warning("Failed to encode image batch: %s", exc)
            finally:
                batch_images.clear()
                batch_slots.clear()

        # Decoding dominates on CPU (JPEG/HEIC -> 224x224 tensor), so decode ahead
        # in a small thread pool while the model works on the previous batch.
        from concurrent.futures import ThreadPoolExecutor

        def load(path: Path):
            try:
                return Image.open(path).convert("RGB")
            except Exception as exc:
                _LOGGER.warning("Failed to load image %s: %s", path, exc)
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            for slot, image in zip(range(len(image_paths)), pool.map(load, image_paths)):
                if image is None:
                    continue
                batch_images.append(image)
                batch_slots.append(slot)
                if len(batch_images) >= batch_size:
                    flush()

        flush()
        return results

    def encode_text(self, text: str) -> Optional[np.ndarray]:
        """Generate embedding for text query."""
        if not self._ensure_loaded():
            return None

        try:
            import torch

            # Process text
            inputs = self._tokenizer(text, return_tensors="pt", padding=True, truncation=True)

            # Generate embedding using text model
            with torch.no_grad():
                outputs = self._model.text_model(**inputs)
                text_embeds = outputs.pooler_output
                text_embeds = self._model.text_projection(text_embeds)

            # Normalize
            embedding = text_embeds[0].numpy()
            embedding = embedding / np.linalg.norm(embedding)

            return embedding.astype(np.float32)

        except Exception as e:
            _LOGGER.warning("Failed to encode text '%s': %s", text, e)
            return None

    def encode_text_cached(self, text: str) -> Optional[np.ndarray]:
        """Encode *text* once and reuse the vector for later searches."""

        cached = self._text_cache.get(text)
        if cached is not None:
            return cached
        vector = self.encode_text(text)
        if vector is not None:
            self._text_cache[text] = vector
        return vector

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
