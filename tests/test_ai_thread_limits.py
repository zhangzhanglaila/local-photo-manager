"""AI inference must not monopolise every core of the machine.

The face and OCR scans start on their own after the file scan and both run
through onnxruntime, whose default is one thread per core. A running scan
therefore pinned the CPU and the interface became unresponsive; the scan is not
user initiated, so this happened on its own.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("onnxruntime", reason="AI scanning needs onnxruntime")

from iPhoto.ai.onnx_threads import (
    DEFAULT_AI_THREAD_LIMIT,
    bounded_session_options,
    thread_limit,
)


def test_thread_limit_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("IPHOTO_FACE_THREADS", raising=False)

    limit = thread_limit()

    assert 1 <= limit <= DEFAULT_AI_THREAD_LIMIT
    cores = os.cpu_count() or 1
    if cores > DEFAULT_AI_THREAD_LIMIT:
        assert limit < cores, "AI inference must leave cores available for the interface"


def test_thread_limit_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("IPHOTO_FACE_THREADS", "2")
    assert thread_limit() == 2

    # Invalid values fall back to the bounded default instead of crashing.
    monkeypatch.setenv("IPHOTO_FACE_THREADS", "not-a-number")
    assert 1 <= thread_limit() <= DEFAULT_AI_THREAD_LIMIT


def test_session_options_are_bounded(monkeypatch) -> None:
    monkeypatch.delenv("IPHOTO_FACE_THREADS", raising=False)

    options = bounded_session_options()

    assert 1 <= options.intra_op_num_threads <= DEFAULT_AI_THREAD_LIMIT
    assert options.inter_op_num_threads == 1


def test_face_pipeline_uses_the_shared_policy(monkeypatch) -> None:
    monkeypatch.setenv("IPHOTO_FACE_THREADS", "3")

    from iPhoto.people.pipeline import _face_session_options

    assert _face_session_options().intra_op_num_threads == 3


def test_rapidocr_sessions_are_bounded(monkeypatch) -> None:
    """RapidOCR builds its sessions internally, so the policy is applied there."""

    pytest.importorskip("rapidocr_onnxruntime", reason="OCR scanning needs RapidOCR")
    monkeypatch.setenv("IPHOTO_FACE_THREADS", "2")

    from iPhoto.ai.onnx_threads import bound_rapidocr_threads

    assert bound_rapidocr_threads() is True

    import rapidocr_onnxruntime.utils as ocr_utils

    options = ocr_utils.SessionOptions()
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 1

    # Applying it twice must not stack wrappers.
    assert bound_rapidocr_threads() is True
    assert ocr_utils.SessionOptions().intra_op_num_threads == 2
