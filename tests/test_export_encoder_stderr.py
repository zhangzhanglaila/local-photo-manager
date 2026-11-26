"""The video encoder must drain ffmpeg's stderr while it streams frames.

Reading stderr only after the last frame let a chatty encoder fill the pipe,
stop reading stdin, and deadlock the export loop.
"""

from __future__ import annotations

import subprocess
import sys
import time

from iPhoto.core import export

_CHATTY_ENCODER = (
    "import sys\n"
    "data = sys.stdin.buffer.read()\n"
    "sys.stderr.write('x' * 200000)\n"
    "sys.stderr.flush()\n"
    "sys.exit(0)\n"
)

_FAILING_ENCODER = (
    "import sys\n"
    "sys.stdin.buffer.read()\n"
    "sys.stderr.write('boom: encoder failed')\n"
    "sys.exit(3)\n"
)


def _spawn(code: str) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    export._start_stderr_drain(process)
    return process


def test_a_large_stderr_does_not_block_the_frame_stream():
    process = _spawn(_CHATTY_ENCODER)
    started = time.monotonic()
    try:
        assert process.stdin is not None
        process.stdin.write(b"0" * (1024 * 1024))
        process.stdin.close()
        message = export._finalise_video_encoder(process)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()

    assert time.monotonic() - started < 30, "the export loop blocked on stderr"
    assert len(message) >= 200_000, "the whole stderr payload must be kept"


def test_a_failing_encoder_surfaces_its_message():
    from iPhoto.errors import ExternalToolError

    process = _spawn(_FAILING_ENCODER)
    try:
        assert process.stdin is not None
        process.stdin.write(b"0")
        process.stdin.close()
        try:
            export._finalise_video_encoder(process)
        except ExternalToolError as exc:
            assert "boom" in str(exc)
        else:  # pragma: no cover - the fake encoder always fails
            raise AssertionError("a non-zero encoder exit must raise")
    finally:
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()
