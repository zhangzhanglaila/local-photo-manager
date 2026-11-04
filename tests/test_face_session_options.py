"""Face inference must not monopolise every core of the machine.

The face scan starts on its own after the file scan, and InsightFace creates its
onnxruntime sessions without options, which defaults to one thread per core. A
scan therefore pinned the CPU and the interface became unresponsive while it ran.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("onnxruntime", reason="face scanning needs onnxruntime")

from iPhoto.people.pipeline import FACE_THREAD_LIMIT, _face_session_options


def test_face_session_threads_are_bounded(monkeypatch) -> None:
    monkeypatch.delenv("IPHOTO_FACE_THREADS", raising=False)

    options = _face_session_options()

    assert 1 <= options.intra_op_num_threads <= FACE_THREAD_LIMIT
    assert options.inter_op_num_threads == 1
    cores = os.cpu_count() or 1
    if cores > FACE_THREAD_LIMIT:
        assert options.intra_op_num_threads < cores, (
            "face inference must leave cores available for the interface"
        )


def test_face_threads_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("IPHOTO_FACE_THREADS", "2")
    assert _face_session_options().intra_op_num_threads == 2

    # Invalid values must fall back to the bounded default instead of crashing.
    monkeypatch.setenv("IPHOTO_FACE_THREADS", "not-a-number")
    assert 1 <= _face_session_options().intra_op_num_threads <= FACE_THREAD_LIMIT
