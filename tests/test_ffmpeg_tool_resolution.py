"""ffmpeg/ffprobe are resolved once, in one place, for every caller."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from iPhoto.core import export
from iPhoto.utils import ffmpeg as ffmpeg_utils


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    ffmpeg_utils.reset_tool_cache()
    yield
    ffmpeg_utils.reset_tool_cache()


def test_env_override_wins(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "custom-ffmpeg.exe"
    binary.write_bytes(b"")
    monkeypatch.setenv(ffmpeg_utils.FFMPEG_ENV, str(binary))
    monkeypatch.setattr(
        ffmpeg_utils.shutil,
        "which",
        lambda _name: pytest.fail("PATH must not be searched when an override is set"),
    )

    assert ffmpeg_utils.ffmpeg_executable() == str(binary)


def test_ffmpeg_and_ffprobe_have_separate_overrides(monkeypatch) -> None:
    monkeypatch.setenv(ffmpeg_utils.FFMPEG_ENV, "/opt/ff/ffmpeg")
    monkeypatch.setenv(ffmpeg_utils.FFPROBE_ENV, "/opt/ff/ffprobe")

    assert ffmpeg_utils.ffmpeg_executable() == "/opt/ff/ffmpeg"
    assert ffmpeg_utils.ffprobe_executable() == "/opt/ff/ffprobe"


def test_falls_back_to_path(monkeypatch) -> None:
    monkeypatch.delenv(ffmpeg_utils.FFMPEG_ENV, raising=False)
    monkeypatch.setattr(
        ffmpeg_utils.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    assert ffmpeg_utils.ffmpeg_executable() == "/usr/bin/ffmpeg"
    assert ffmpeg_utils.ffprobe_executable() == "/usr/bin/ffprobe"


def test_falls_back_to_the_bare_name_when_path_has_no_build(monkeypatch) -> None:
    monkeypatch.delenv(ffmpeg_utils.FFMPEG_ENV, raising=False)
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda _name: None)

    assert ffmpeg_utils.ffmpeg_executable() == "ffmpeg"


def test_resolution_is_cached_until_reset(monkeypatch) -> None:
    calls: list[str] = []

    def _which(name: str) -> str:
        calls.append(name)
        return f"/first/{name}"

    monkeypatch.delenv(ffmpeg_utils.FFMPEG_ENV, raising=False)
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", _which)

    assert ffmpeg_utils.ffmpeg_executable() == "/first/ffmpeg"
    assert ffmpeg_utils.ffmpeg_executable() == "/first/ffmpeg"
    assert calls == ["ffmpeg"]

    ffmpeg_utils.reset_tool_cache()
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: f"/second/{name}")
    assert ffmpeg_utils.ffmpeg_executable() == "/second/ffmpeg"


def test_hidden_process_options_match_the_platform() -> None:
    startupinfo, creationflags = ffmpeg_utils.hidden_process_options()

    if os.name == "nt":
        assert startupinfo is not None
        assert isinstance(creationflags, int)
    else:
        assert startupinfo is None
        assert creationflags == 0


def test_probe_uses_the_resolved_ffprobe(monkeypatch) -> None:
    captured: list[list[str]] = []

    def _run(command, **_kwargs):
        captured.append(list(command))
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", _run)
    monkeypatch.setenv(ffmpeg_utils.FFPROBE_ENV, "/opt/ff/ffprobe")

    ffmpeg_utils._probe_media_uncached(ffmpeg_utils.Path("clip.mp4"))

    assert captured[0][0] == "/opt/ff/ffprobe"


def test_video_export_launches_the_resolved_ffmpeg(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def _popen(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdin=None, stderr=None, poll=lambda: 0)

    monkeypatch.setattr(export.subprocess, "Popen", _popen)
    monkeypatch.setattr(export, "ffmpeg_executable", lambda: "/opt/ff/ffmpeg")

    export._start_video_encoder(
        source=tmp_path / "clip.mov",
        destination=tmp_path / "out.mp4",
        width=64,
        height=48,
        fps=30.0,
        trim_in_sec=0.0,
        trim_out_sec=0.0,
    )

    command = captured["command"]
    assert command[0] == "/opt/ff/ffmpeg"
