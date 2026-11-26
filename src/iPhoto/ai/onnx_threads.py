"""Thread policy for onnxruntime-based AI work.

The face and OCR scans start on their own once the file scan finishes, and both
run through onnxruntime. Its default is ``intra_op_num_threads=0``, meaning "one
thread per core": on a 24 core machine a single scan opened 24 inference threads
and the interface became unresponsive while it ran.

The limit lives here so both scans follow the same rule instead of each choosing
its own.
"""

from __future__ import annotations

import os

DEFAULT_AI_THREAD_LIMIT = 2
THREAD_LIMIT_ENV = "IPHOTO_FACE_THREADS"


def thread_limit(env_var: str = THREAD_LIMIT_ENV) -> int:
    """Return how many inference threads one AI task may use."""

    limit = max(1, min(DEFAULT_AI_THREAD_LIMIT, (os.cpu_count() or DEFAULT_AI_THREAD_LIMIT) // 2))
    override = os.environ.get(env_var, "").strip()
    if override.isdigit() and int(override) > 0:
        limit = int(override)
    return limit


def bounded_session_options(env_var: str = THREAD_LIMIT_ENV):
    """Build onnxruntime session options that leave cores for the interface."""

    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = thread_limit(env_var)
    options.inter_op_num_threads = 1
    return options


def rapidocr_engine_params(env_var: str = THREAD_LIMIT_ENV) -> dict[str, int]:
    """Return the RapidOCR 3.x parameters that bound its inference threads.

    The 3.x package exposes ``EngineConfig.onnxruntime.*``, so the policy is
    passed in rather than patched into its module (which only the 1.x wheel
    needed).
    """

    return {
        "EngineConfig.onnxruntime.intra_op_num_threads": thread_limit(env_var),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }


def bound_rapidocr_threads(env_var: str = THREAD_LIMIT_ENV) -> bool:
    """Return whether the bounded policy can be applied to RapidOCR.

    Kept as the single entry point callers/tests use; the actual limit is handed
    to the engine through :func:`rapidocr_engine_params`.
    """

    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return bool(rapidocr_engine_params(env_var))


__all__ = [
    "DEFAULT_AI_THREAD_LIMIT",
    "THREAD_LIMIT_ENV",
    "bounded_session_options",
    "bound_rapidocr_threads",
    "rapidocr_engine_params",
    "thread_limit",
]
