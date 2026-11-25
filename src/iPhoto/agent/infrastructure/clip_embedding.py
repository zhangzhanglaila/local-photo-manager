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


def _preferred_device_and_dtype(torch):
    """Return ``(device, dtype)`` for this machine.

    The CUDA build of torch still runs on CPU when no device is present, and
    ``IPHOTO_DISABLE_CUDA=1`` forces the CPU path for testing or for machines
    where the driver is broken.
    """

    import os

    disabled = os.environ.get("IPHOTO_DISABLE_CUDA", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    try:
        if not disabled and torch.cuda.is_available():
            # Half precision halves VRAM and is faster on every RTX part; the
            # cosine drift versus fp32 is ~1e-3.
            return "cuda", torch.float16
    except Exception:  # pragma: no cover - driver problems must not break loading
        pass
    return "cpu", torch.float32


def _classes_for_family(family: str):
    """Return ``(model_cls, processor_cls, tokenizer_cls)`` for *family*."""

    if family == "siglip":
        from transformers import AutoProcessor, SiglipModel

        return SiglipModel, AutoProcessor, None
    if family == "chinese_clip":
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

        return ChineseCLIPModel, ChineseCLIPProcessor, None

    from transformers import CLIPModel, CLIPProcessor, CLIPTokenizer

    return CLIPModel, CLIPProcessor, CLIPTokenizer


def _image_features(model, inputs):
    """Return the projected image features of *model* as a tensor."""

    getter = getattr(model, "get_image_features", None)
    if callable(getter):
        try:
            return getter(**inputs)
        except TypeError:
            pass
    outputs = model.vision_model(**inputs)
    return model.visual_projection(outputs.pooler_output)


def _text_features(model, inputs):
    """Return the projected text features of *model* as a tensor."""

    getter = getattr(model, "get_text_features", None)
    if callable(getter):
        try:
            return getter(**inputs)
        except TypeError:
            pass
    outputs = model.text_model(**inputs)
    return model.text_projection(outputs.pooler_output)


def _move_inputs(inputs, device, dtype):
    """Move processor/tokenizer tensors onto *device* with *dtype* when floating."""

    moved = {}
    for key, value in inputs.items():
        if hasattr(value, "to"):
            target_dtype = dtype if getattr(value, "is_floating_point", lambda: False)() else None
            moved[key] = value.to(device, target_dtype) if target_dtype is not None else value.to(device)
        else:
            moved[key] = value
    return moved


class CLIPEmbeddingService:
    """CLIP embedding service using transformers.

    This service loads CLIP model and provides methods for encoding
    images and text into embeddings.
    """

    def __init__(self, model_dir: Path, family: str = "clip") -> None:
        """Initialize the embedding service.

        Parameters
        ----------
        model_dir : Path
            Directory containing the model files.
        family : str
            ``"clip"``, ``"siglip"`` or ``"chinese_clip"``. The three families differ
            in class names, tokenizer padding and output dimension, not in how the
            app uses the vectors.
        """
        self._model_dir = Path(model_dir)
        self._family = family if family in {"clip", "siglip", "chinese_clip"} else "clip"
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._loaded = False
        # Text vectors are deterministic; caching them keeps repeated searches and
        # the label vocabulary from re-encoding the same strings.
        self._text_cache: dict[str, np.ndarray] = {}
        # Chosen on load: GPU when a CUDA device is present, otherwise CPU. The
        # model file is the same either way, only precision/batch differ.
        self._device = "cpu"
        self._dtype = None

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

                    _LOGGER.info(
                        "Loading %s model from %s", self._family, self._model_dir
                    )

                    device, dtype = _preferred_device_and_dtype(torch)
                    _LOGGER.info(
                        "CLIP device=%s dtype=%s", device, getattr(dtype, "__name__", dtype)
                    )
                    model_cls, processor_cls, tokenizer_cls = _classes_for_family(
                        self._family
                    )
                    # Load model with optimizations
                    self._model = model_cls.from_pretrained(
                        str(self._model_dir),
                        torch_dtype=dtype,
                    )
                    self._model.to(device)
                    self._device = device
                    self._dtype = dtype
                    self._processor = processor_cls.from_pretrained(str(self._model_dir))
                    if tokenizer_cls is not None:
                        self._tokenizer = tokenizer_cls.from_pretrained(str(self._model_dir))
                    else:
                        self._tokenizer = getattr(self._processor, "tokenizer", None)

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
            inputs = _move_inputs(inputs, self._device, self._dtype)

            # Generate embedding using the model's own image tower
            with torch.no_grad():
                image_embeds = _image_features(self._model, inputs)

            # Normalize
            embedding = image_embeds[0].float().cpu().numpy()
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
                inputs = {key: torch.stack([item[key] for item in batch_images]) for key in batch_images[0]}
                inputs = _move_inputs(inputs, self._device, self._dtype)
                with torch.no_grad():
                    embeds = _image_features(self._model, inputs)
                array = embeds.float().cpu().numpy()
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

        # Decoding *and* preprocessing dominate (JPEG/HEIC -> 224x224 tensor is
        # ~85% of the wall time for CLIP B/32), and both release the GIL, so they
        # run in a thread pool while the GPU computes the previous batch.
        from concurrent.futures import ThreadPoolExecutor

        def load(path: Path):
            try:
                image = Image.open(path)
                # JPEG decoders can decode at a reduced scale; skipping the full
                # 4000px decode is the single biggest win on phone photos.
                try:
                    image.draft("RGB", (256, 256))
                except Exception:
                    pass
                prepared = self._processor(images=image.convert("RGB"), return_tensors="pt")
                return {key: value[0] for key, value in prepared.items()}
            except Exception as exc:
                _LOGGER.warning("Failed to load image %s: %s", path, exc)
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
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

            # Process text. SigLIP has a fixed 64-token context and expects
            # max-length padding; CLIP-family models take dynamic padding.
            padding = "max_length" if self._family == "siglip" else True
            processor = self._processor if self._family != "clip" else self._tokenizer
            inputs = processor(text, return_tensors="pt", padding=padding, truncation=True)
            inputs = _move_inputs(inputs, self._device, self._dtype)

            # Generate embedding using the model's own text tower
            with torch.no_grad():
                text_embeds = _text_features(self._model, inputs)

            # Normalize
            embedding = text_embeds[0].float().cpu().numpy()
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
