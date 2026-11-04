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

DEFAULT_AI_THREAD_LIMIT = 4
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


def bound_rapidocr_threads(env_var: str = THREAD_LIMIT_ENV) -> bool:
    """Make RapidOCR build its sessions with the bounded thread policy.

    RapidOCR constructs ``SessionOptions()`` inside its own module and keeps no
    knob for the thread count, so the only place to apply the policy is the name
    it resolves at session creation time. Returns ``True`` when the patch is in
    place (installed now or installed earlier).
    """

    try:
        import rapidocr_onnxruntime.utils as ocr_utils
    except ImportError:
        return False

    if getattr(ocr_utils, "_iphotro_bounded_threads", False):
        return True

    original = ocr_utils.SessionOptions

    def factory(*args, **kwargs):
        options = original(*args, **kwargs)
        options.intra_op_num_threads = thread_limit(env_var)
        options.inter_op_num_threads = 1
        return options

    ocr_utils.SessionOptions = factory
    ocr_utils._iphotro_bounded_threads = True  # type: ignore[attr-defined]
    return True
