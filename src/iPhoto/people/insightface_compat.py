"""Runtime shims that keep insightface working across numpy/typing versions.

These patches used to live at the bottom of ``people/pipeline.py``, mixed in with
the clustering code. They monkey-patch third-party modules at import time, which
is exactly the kind of thing that should be readable on its own: when a new
insightface or numpy release breaks face detection, this is the file to read.

Bodies moved verbatim; ``pipeline.py`` re-imports the names so its call sites did
not change.
"""

from __future__ import annotations

import builtins
import logging
import sys
import typing
from types import ModuleType

import numpy as np


_LOGGER = logging.getLogger(__name__)


def _build_face_analysis_init_error(
    *,
    feature_name: str,
    model_pack: str,
    model_dir: Path,
    exc: Exception,
) -> RuntimeError:
    reason = str(exc).strip() or exc.__class__.__name__
    model_pack_dir = model_dir / model_pack
    if not model_pack_dir.exists():
        return RuntimeError(
            f"{feature_name} unavailable: InsightFace model '{model_pack}' is not cached at "
            f"'{model_pack_dir}'. Initialization/download failed ({reason}). "
            f"Allow one download from github.com or copy an existing '{model_pack}' model "
            f"folder into '{model_dir}', then retry."
        )
    return RuntimeError(
        f"{feature_name} unavailable: failed to initialize InsightFace model "
        f"'{model_pack}' from '{model_pack_dir}' ({reason})."
    )

def _face_session_options():
    """Return bounded onnxruntime session options for face inference.

    InsightFace builds its sessions through ``onnxruntime.InferenceSession``
    without options, which defaults to one intra-op thread per core. A single
    face scan therefore pinned every core of the machine, and the interface
    became unresponsive for as long as the scan ran — the face scan starts on its
    own once the file scan finishes, so this happened without the user asking for
    it. The shared policy in :mod:`iPhoto.ai.onnx_threads` keeps face and OCR
    scans consistent, and ``IPHOTO_FACE_THREADS`` overrides the limit for users
    who would rather finish a scan quickly.
    """

    from iPhoto.ai.onnx_threads import THREAD_LIMIT_ENV, bounded_session_options

    return bounded_session_options(THREAD_LIMIT_ENV)

def _resolve_execution_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return ["CPUExecutionProvider"]

    available = ort.get_available_providers()
    providers: list[str] = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in available:
        providers.append("CPUExecutionProvider")
    return providers or ["CPUExecutionProvider"]

def _patch_insightface_alignment_estimate() -> None:
    try:
        from insightface.utils import face_align
        from skimage import transform as trans
    except ImportError:
        return

    if getattr(face_align, "_iphoto_from_estimate_patch", False):
        return

    similarity_transform_cls = getattr(trans, "SimilarityTransform", None)
    from_estimate = getattr(similarity_transform_cls, "from_estimate", None)
    if from_estimate is None:
        return

    def estimate_norm(lmk, image_size=112, mode="arcface"):
        del mode
        assert lmk.shape == (5, 2)
        assert image_size % 112 == 0 or image_size % 128 == 0
        if image_size % 112 == 0:
            ratio = float(image_size) / 112.0
            diff_x = 0.0
        else:
            ratio = float(image_size) / 128.0
            diff_x = 8.0 * ratio

        dst = face_align.arcface_dst * ratio
        dst[:, 0] += diff_x
        tform = similarity_transform_cls.from_estimate(lmk, dst)
        return tform.params[0:2, :]

    face_align.estimate_norm = estimate_norm
    face_align._iphoto_from_estimate_patch = True

def _install_insightface_mask_renderer_stubs() -> None:
    """Avoid importing albumentations for InsightFace mask rendering we do not use."""
    if "albumentations" in sys.modules:
        return

    albumentations_module = ModuleType("albumentations")
    core_module = ModuleType("albumentations.core")
    transforms_module = ModuleType("albumentations.core.transforms_interface")

    class ImageOnlyTransform:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

    transforms_module.ImageOnlyTransform = ImageOnlyTransform
    core_module.transforms_interface = transforms_module
    albumentations_module.core = core_module

    sys.modules["albumentations"] = albumentations_module
    sys.modules["albumentations.core"] = core_module
    sys.modules["albumentations.core.transforms_interface"] = transforms_module

def _install_runtime_typing_compat() -> None:
    """Provide typing names some third-party annotations expect at runtime."""
    import numpy.typing as npt

    compat_names = {
        "Any": typing.Any,
        "Callable": typing.Callable,
        "ClassVar": typing.ClassVar,
        "Concatenate": typing.Concatenate,
        "Dict": typing.Dict,
        "Final": typing.Final,
        "Generic": typing.Generic,
        "Iterable": typing.Iterable,
        "List": typing.List,
        "Literal": typing.Literal,
        "LiteralString": typing.LiteralString,
        "Mapping": typing.Mapping,
        "MutableMapping": typing.MutableMapping,
        "Never": typing.Never,
        "NoReturn": typing.NoReturn,
        "NotRequired": typing.NotRequired,
        "Optional": typing.Optional,
        "ParamSpec": typing.ParamSpec,
        "Protocol": typing.Protocol,
        "Required": typing.Required,
        "Self": typing.Self,
        "Sequence": typing.Sequence,
        "Set": typing.Set,
        "TypedDict": typing.TypedDict,
        "Tuple": typing.Tuple,
        "TypeAlias": typing.TypeAlias,
        "TypeGuard": typing.TypeGuard,
        "TypeVar": typing.TypeVar,
        "Union": typing.Union,
        "ArrayLike": npt.ArrayLike,
        "DTypeLike": npt.DTypeLike,
        "NDArray": npt.NDArray,
        "ndarray": np.ndarray,
    }
    for name, value in compat_names.items():
        if not hasattr(builtins, name):
            setattr(builtins, name, value)


__all__ = [
    "_build_face_analysis_init_error",
    "_face_session_options",
    "_install_insightface_mask_renderer_stubs",
    "_install_runtime_typing_compat",
    "_patch_insightface_alignment_estimate",
    "_resolve_execution_providers",
]
