"""Batched image encoding for the semantic index."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch", reason="torch is required for CLIP")
pytest.importorskip("PIL", reason="Pillow is required for image decoding")

from iPhoto.agent.infrastructure.clip_embedding import CLIPEmbeddingService


def _service(batch_calls: list[int]):
    """Build a service whose model is a stub that records model batch sizes."""

    import torch

    service = CLIPEmbeddingService.__new__(CLIPEmbeddingService)
    service._loaded = True
    service._ensure_loaded = lambda: True  # type: ignore[method-assign]
    service._device = "cpu"
    service._dtype = torch.float32

    def processor(*, images, return_tensors):
        # The pipeline preprocesses one image per worker thread.
        return {"pixel_values": torch.zeros(1, 3, 4, 4)}

    def vision_model(**kwargs):
        count = int(kwargs["pixel_values"].shape[0])
        batch_calls.append(count)
        return SimpleNamespace(pooler_output=torch.arange(count * 4, dtype=torch.float32).reshape(count, 4))

    service._processor = processor  # type: ignore[assignment]
    service._model = SimpleNamespace(
        vision_model=vision_model,
        visual_projection=lambda tensor: tensor,
    )  # type: ignore[assignment]
    return service


def _images(tmp_path: Path, count: int) -> list[Path]:
    from PIL import Image

    paths = []
    for index in range(count):
        path = tmp_path / f"img_{index}.png"
        Image.new("RGB", (8, 8), (index * 10 % 255, 40, 90)).save(path)
        paths.append(path)
    return paths


def test_images_are_encoded_in_batches(tmp_path: Path) -> None:
    calls: list[int] = []
    service = _service(calls)
    paths = _images(tmp_path, 20)

    embeddings = service.encode_images(paths, batch_size=16)

    assert calls == [16, 4], calls
    assert len(embeddings) == 20
    assert all(vector is not None for vector in embeddings)
    assert all(abs(float(np.linalg.norm(vector)) - 1.0) < 1e-5 for vector in embeddings)


def test_a_broken_file_only_loses_its_own_slot(tmp_path: Path) -> None:
    calls: list[int] = []
    service = _service(calls)
    paths = _images(tmp_path, 3)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    paths.insert(1, broken)

    embeddings = service.encode_images(paths, batch_size=16)

    assert len(embeddings) == 4
    assert embeddings[1] is None
    assert embeddings[0] is not None and embeddings[2] is not None


def test_empty_input_is_a_no_op(tmp_path: Path) -> None:
    calls: list[int] = []
    service = _service(calls)

    assert service.encode_images([]) == []
    assert calls == []


def test_device_selection_prefers_cuda_but_can_be_disabled(monkeypatch):
    import torch

    from iPhoto.agent.infrastructure.clip_embedding import _preferred_device_and_dtype

    class _Cuda:
        @staticmethod
        def is_available():
            return True

    fake = SimpleNamespace(cuda=_Cuda(), float16="fp16", float32="fp32")

    monkeypatch.delenv("IPHOTO_DISABLE_CUDA", raising=False)
    assert _preferred_device_and_dtype(fake) == ("cuda", "fp16")

    monkeypatch.setenv("IPHOTO_DISABLE_CUDA", "1")
    assert _preferred_device_and_dtype(fake) == ("cpu", "fp32")

    class _NoCuda:
        @staticmethod
        def is_available():
            return False

    monkeypatch.delenv("IPHOTO_DISABLE_CUDA", raising=False)
    assert _preferred_device_and_dtype(SimpleNamespace(cuda=_NoCuda(), float32="fp32")) == ("cpu", "fp32")
