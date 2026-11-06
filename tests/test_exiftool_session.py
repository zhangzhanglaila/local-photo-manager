"""ExifTool runs as a persistent process, with the one-shot path as a fallback.

Launching ExifTool costs ~410 ms; the info panel used to pay that on every open.
``-stay_open`` keeps the interpreter alive (~20 ms per request), but the session
must never become a correctness dependency — any failure has to fall back to the
original one-shot invocation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from iPhoto.utils import exiftool
from iPhoto.utils.exiftool import get_metadata_batch

_FIXTURE = Path(__file__).parent / "fixtures" / "fake_exiftool.py"


def _write_wrapper(tmp_path: Path) -> Path:
    """Create an executable that mimics the ``exiftool`` command line."""

    if os.name == "nt":
        wrapper = tmp_path / "fake-exiftool.cmd"
        wrapper.write_text(
            f'@echo off\n"{sys.executable}" "{_FIXTURE}" %*\n', encoding="utf-8"
        )
    else:
        wrapper = tmp_path / "fake-exiftool"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{_FIXTURE}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return wrapper


@pytest.fixture()
def fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wrapper = _write_wrapper(tmp_path)
    log = tmp_path / "starts.log"
    monkeypatch.setenv("IPHOTO_EXIFTOOL_PATH", str(wrapper))
    monkeypatch.setenv("FAKE_EXIFTOOL_LOG", str(log))
    monkeypatch.delenv("FAKE_EXIFTOOL_EXIT_AFTER", raising=False)
    exiftool._close_session()
    try:
        yield log
    finally:
        exiftool._close_session()


def _starts(log: Path) -> int:
    if not log.exists():
        return 0
    return len(log.read_text(encoding="utf-8").splitlines())


def test_requests_reuse_one_process(fake_tool, tmp_path: Path) -> None:
    photos = [tmp_path / "a.jpg", tmp_path / "b.jpg"]

    first = get_metadata_batch(photos)
    second = get_metadata_batch(photos)

    assert len(first) == 2 and len(second) == 2
    assert first[0]["Fake"] is True
    assert _starts(fake_tool) == 1, "the persistent process must be reused"


def test_batches_stay_correct_through_the_session(fake_tool, tmp_path: Path) -> None:
    photos = [tmp_path / f"{index}.jpg" for index in range(30)]

    rows = get_metadata_batch(photos)

    # Every requested photo is returned exactly once (the stub echoes the paths it
    # receives, so an accidental extra argument would show up here).
    returned = [row["SourceFile"] for row in rows if row["SourceFile"].endswith(".jpg")]
    assert len(returned) == 30
    assert set(returned) == {photo.absolute().as_posix() for photo in photos}


def test_falls_back_to_one_shot_when_disabled(
    fake_tool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IPHOTO_EXIFTOOL_STAY_OPEN", "0")
    photos = [tmp_path / "a.jpg"]

    assert len(get_metadata_batch(photos)) == 1
    assert len(get_metadata_batch(photos)) == 1

    # Two invocations means no session was kept alive.
    assert _starts(fake_tool) == 2


def test_recovers_from_a_crashed_session(
    fake_tool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = [tmp_path / "a.jpg"]

    # The stub exits after the first request, so the next call finds a dead
    # process and must transparently fall back to the one-shot path.
    monkeypatch.setenv("FAKE_EXIFTOOL_EXIT_AFTER", "1")
    get_metadata_batch(photos)

    rows = get_metadata_batch(photos)

    assert len(rows) == 1
    assert rows[0]["Fake"] is True
