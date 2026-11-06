"""Temporary diagnostics must be opt-in.

Debug instrumentation was written unconditionally at INFO (the video-trim path at
WARNING), so a normal session filled the log with internal step markers and buried
real warnings.
"""

from __future__ import annotations

import logging

import pytest

from iPhoto.utils import logging as logging_utils


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(logging_utils.DIAG_ENV, raising=False)
    yield
    monkeypatch.delenv(logging_utils.DIAG_ENV, raising=False)


def test_diagnostics_are_silent_by_default(caplog: pytest.LogCaptureFixture) -> None:
    # Diagnostics also switch on when the app log level is DEBUG; keep INFO here so
    # this test exercises the default (disabled) path.
    with caplog.at_level(logging.INFO, logger="iPhoto"):
        logging_utils.diag("[DIAG] step %s", 1)

    assert caplog.records == []


def test_diagnostics_emit_when_enabled(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(logging_utils.DIAG_ENV, "1")
    with caplog.at_level(logging.DEBUG, logger="iPhoto"):
        logging_utils.diag("[DIAG] step %s", 1)

    assert [record.getMessage() for record in caplog.records] == ["[DIAG] step 1"]


def test_unrecognised_value_keeps_diagnostics_off(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(logging_utils.DIAG_ENV, "maybe")

    with caplog.at_level(logging.INFO, logger="iPhoto"):
        logging_utils.diag("[DIAG] step")

    assert caplog.records == []
