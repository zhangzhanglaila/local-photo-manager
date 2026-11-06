"""Process-wide CPU budget for background work.

The face scan used to open one inference thread per core; capping onnxruntime
fixed part of that, but three other thread pools were never limited:

* OpenCV's ``parallel_for`` (InsightFace calls ``warpAffine``/``resize`` on every
  face),
* BLAS backends behind NumPy (OpenBLAS/MKL each spawn a full-width pool),
* libheif/HEIC decoding.

On a 24 core machine the scan still burned ~22 cores with the whole application
running at 90% of the machine. This module caps all of them with one budget so
the interface — and every other application — keeps room to run.

Set ``IPHOTO_MAX_BACKGROUND_THREADS`` to override the budget.
"""

from __future__ import annotations

import os

BUDGET_ENV = "IPHOTO_MAX_BACKGROUND_THREADS"
DEFAULT_BUDGET = 4

#: Environment variables read by BLAS/OpenMP runtimes at import time. They must
#: be set before NumPy/OpenCV are imported, which is why the GUI entry point calls
#: :func:`apply_early_thread_limits` before importing anything heavy.
_BLAS_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def background_budget() -> int:
    """Return how many threads background work may use in total."""

    override = os.environ.get(BUDGET_ENV, "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    cpu = os.cpu_count() or DEFAULT_BUDGET
    return max(1, min(DEFAULT_BUDGET, cpu))


def apply_early_thread_limits(budget: int | None = None) -> int:
    """Limit BLAS/OpenMP pools. Call this before importing NumPy or OpenCV."""

    limit = budget if budget is not None else background_budget()
    for name in _BLAS_ENV_VARS:
        # Respect an explicit user override, otherwise pin the pool.
        if not os.environ.get(name, "").strip():
            os.environ[name] = str(limit)
    return limit


def apply_library_thread_limits(budget: int | None = None) -> int:
    """Limit OpenCV's internal pool; call after ``cv2`` is importable.

    OpenCV does not read the environment variables above, so it needs its own
    call. Failures are ignored: a missing or stubbed OpenCV must never break the
    scan.
    """

    limit = budget if budget is not None else background_budget()
    try:
        import cv2  # local import: optional dependency
    except Exception:  # pragma: no cover - depends on the environment
        return limit
    try:
        cv2.setNumThreads(limit)
    except Exception:  # pragma: no cover - defensive
        pass
    return limit


__all__ = [
    "BUDGET_ENV",
    "DEFAULT_BUDGET",
    "apply_early_thread_limits",
    "apply_library_thread_limits",
    "background_budget",
]
