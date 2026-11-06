"""Background work must share one CPU budget.

onnxruntime was capped, but OpenCV's ``parallel_for`` and the BLAS backends behind
NumPy each keep a full-width pool of their own; on a 24 core machine a single face
scan still burned ~22 cores and the interface (and every other application) lost
the machine.
"""

from __future__ import annotations

from iPhoto.utils import thread_limits


def _clear(monkeypatch) -> None:
    for name in thread_limits._BLAS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(thread_limits.BUDGET_ENV, raising=False)


def test_default_budget_is_applied_to_every_runtime(monkeypatch) -> None:
    _clear(monkeypatch)

    limit = thread_limits.apply_early_thread_limits()

    assert limit == thread_limits.DEFAULT_BUDGET
    for name in thread_limits._BLAS_ENV_VARS:
        assert thread_limits.os.environ.get(name) == str(limit)


def test_explicit_override_is_honoured(monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(thread_limits.BUDGET_ENV, "2")

    limit = thread_limits.apply_early_thread_limits()

    assert limit == 2
    assert thread_limits.os.environ["OMP_NUM_THREADS"] == "2"


def test_user_environment_is_not_overwritten(monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("OMP_NUM_THREADS", "8")

    thread_limits.apply_early_thread_limits()

    assert thread_limits.os.environ["OMP_NUM_THREADS"] == "8", "respect explicit user config"
    # Other runtimes still follow the budget.
    assert thread_limits.os.environ["OPENBLAS_NUM_THREADS"] == str(thread_limits.DEFAULT_BUDGET)


def test_opencv_pool_is_capped(monkeypatch) -> None:
    calls: list[int] = []

    class _FakeCv2:
        @staticmethod
        def setNumThreads(value: int) -> None:  # noqa: N802 - OpenCV API name
            calls.append(value)

    import sys

    monkeypatch.setitem(sys.modules, "cv2", _FakeCv2)

    limit = thread_limits.apply_library_thread_limits(3)

    assert limit == 3
    assert calls == [3]


def test_missing_opencv_is_not_fatal(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "cv2", None)

    # A stub/absent OpenCV must never break a scan.
    assert thread_limits.apply_library_thread_limits(4) == 4
