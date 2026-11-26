"""Which processor each AI model actually runs on.

The settings rows say "CUDA" or "CPU" so a user can tell at a glance whether the
GPU is being used, without reading log files.

* Semantic search runs on PyTorch, which has a CUDA build here.
* Faces and OCR run on ONNX Runtime. That dependency is the CPU-only build in
  this environment, so both report "CPU" even on a machine with a GPU.
"""

from __future__ import annotations

import logging
import sys

_LOGGER = logging.getLogger(__name__)


def search_device(status: "object | None" = None) -> str:
    """Return ``"CUDA"`` or ``"CPU"`` for the semantic-search encoder.

    *status* is the injected :class:`SearchIndexStatus` when the caller has one;
    it knows the device the encoder actually selected. Without it, torch's own
    capability check is the best available answer.
    """

    device = getattr(status, "device", "") if status is not None else ""
    if device:
        return str(device).upper()

    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            return "CUDA" if torch.cuda.is_available() else "CPU"
        except Exception:  # pragma: no cover - driver issues
            return "CPU"
    # Not loaded yet: the encoder picks CUDA when torch can see a device.
    return "CUDA" if _torch_has_cuda() else "CPU"


def _torch_has_cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover - torch may be missing
        return False


def onnx_device() -> str:
    """Return ``"CUDA"`` or ``"CPU"`` for the ONNX Runtime based scans."""

    try:
        import onnxruntime as ort

        providers = set(ort.get_available_providers())
    except Exception:  # pragma: no cover - onnxruntime may be missing
        _LOGGER.debug("onnxruntime unavailable for device detection", exc_info=True)
        return "CPU"
    return "CUDA" if "CUDAExecutionProvider" in providers else "CPU"


__all__ = ["onnx_device", "search_device"]
