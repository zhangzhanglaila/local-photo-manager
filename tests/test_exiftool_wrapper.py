"""The ExifTool session must not leave Perl processes behind."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from iPhoto.utils import exiftool


def test_batch_wrapper_is_reduced_to_a_direct_perl_call(tmp_path: Path) -> None:
    perl = tmp_path / "perl.exe"
    perl.write_bytes(b"")
    script = tmp_path / "exiftool"
    script.write_text("# perl script", encoding="utf-8")
    batch = tmp_path / "exiftool.bat"
    batch.write_text(
        "@echo off\n"
        f'"{perl}" -I"{tmp_path / "lib"}" "{script}" %*\n',
        encoding="utf-8",
    )

    argv = exiftool.argv_from_wrapper(str(batch))

    assert argv == [str(perl), f"-I{tmp_path / 'lib'}", str(script)]
    # No ``cmd.exe`` in the chain means only one process to reap.
    assert exiftool.launch_argv(str(batch)) == argv


def test_plain_executables_are_launched_as_is(tmp_path: Path) -> None:
    executable = tmp_path / "exiftool.exe"
    executable.write_bytes(b"")

    assert exiftool.argv_from_wrapper(str(executable)) is None
    assert exiftool.launch_argv(str(executable)) == [str(executable)]


def test_a_batch_that_cannot_be_parsed_is_used_verbatim(tmp_path: Path) -> None:
    batch = tmp_path / "weird.bat"
    batch.write_text("@echo off\ncall something_else.cmd %*\n", encoding="utf-8")

    assert exiftool.argv_from_wrapper(str(batch)) is None
    assert exiftool.launch_argv(str(batch)) == [str(batch)]


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows-only")
def test_children_are_adopted_by_the_kill_on_close_job(tmp_path: Path) -> None:
    if exiftool._kill_on_close_job() is None:
        pytest.skip("job objects are unavailable in this environment")

    # A child adopted by the job dies with its parent instead of lingering as an
    # idle ExifTool interpreter (the leak this fix is about).
    import subprocess

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        exiftool._adopt(process)
        assert process.poll() is None
    finally:
        exiftool._kill_tree(process)
        process.wait(timeout=10)
    assert process.poll() is not None


def test_closing_the_session_reaps_the_process(tmp_path: Path) -> None:
    executable = exiftool._resolve_exiftool_executable()
    session = exiftool._ExifToolSession(executable)
    process = session._process
    try:
        assert process.poll() is None
    finally:
        session.close()

    assert process.poll() is not None, "the interpreter outlived its session"
