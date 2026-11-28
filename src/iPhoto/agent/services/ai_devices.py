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
from importlib.metadata import PackageNotFoundError, version

_LOGGER = logging.getLogger(__name__)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except (PackageNotFoundError, ValueError):
        return ""


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

    # Do not import torch just to label the settings row: importing it takes
    # hundreds of milliseconds and holds the import lock, which froze the first
    # paint of the settings window. CUDA wheels carry ``+cuXXX`` in their
    # distribution version; the live status object replaces this guess once the
    # encoder actually starts.
    version_text = _package_version("torch").lower()
    return "CUDA" if "+cu" in version_text else "CPU"


def onnx_device() -> str:
    """Return ``"CUDA"`` or ``"CPU"`` for the ONNX Runtime based scans."""

    ort = sys.modules.get("onnxruntime")
    if ort is not None:
        try:
            providers = set(ort.get_available_providers())
        except Exception:  # pragma: no cover - driver issues
            return "CPU"
        return "CUDA" if "CUDAExecutionProvider" in providers else "CPU"

    # As above, avoid importing onnxruntime for a label. The CUDA build is
    # distributed as ``onnxruntime-gpu``; the plain package is CPU-only.
    return "CUDA" if _package_version("onnxruntime-gpu") else "CPU"


__all__ = ["onnx_device", "search_device"]
