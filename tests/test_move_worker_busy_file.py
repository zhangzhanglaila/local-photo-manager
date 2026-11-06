"""Moving a file that a background read still holds open must self-heal.

Windows reports a locked file as ``WinError 32``. Since metadata reads now keep a
persistent ExifTool process alive, a move/delete can collide with an in-flight
read; the worker releases those handles and retries once instead of failing.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from iPhoto.gui.ui.tasks import move_worker as move_worker_module
from iPhoto.gui.ui.tasks.move_worker import MoveSignals, MoveWorker
from iPhoto.utils.pathutils import is_file_busy_error


class _Busy(OSError):
    """OSError that Windows would report as "file is in use"."""

    def __init__(self) -> None:
        super().__init__(errno.EACCES, "file is in use")
        self.winerror = 32


@pytest.mark.parametrize(
    "exc, expected",
    [
        (_Busy(), True),
        (PermissionError(errno.EACCES, "denied"), True),
        (OSError(errno.EBUSY, "busy"), True),
        (OSError(errno.ENOENT, "missing"), False),
        (OSError(errno.ENOSPC, "no space"), False),
    ],
)
def test_is_file_busy_error(exc: OSError, expected: bool) -> None:
    assert is_file_busy_error(exc) is expected


@pytest.fixture()
def worker(tmp_path: Path) -> MoveWorker:
    return MoveWorker(
        sources=[],
        source_root=tmp_path,
        destination_root=tmp_path / "dest",
        signals=MoveSignals(),
    )


def test_move_retries_after_releasing_metadata_handles(
    worker: MoveWorker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"x")
    target = tmp_path / "dest" / "a.jpg"
    target.parent.mkdir()

    released: list[bool] = []
    monkeypatch.setattr(
        move_worker_module, "release_metadata_handles", lambda: released.append(True)
    )

    attempts: list[int] = []
    real_move = move_worker_module.shutil.move

    def flaky_move(src, dst):
        attempts.append(1)
        if len(attempts) == 1:
            raise _Busy()
        return real_move(src, dst)

    monkeypatch.setattr(move_worker_module.shutil, "move", flaky_move)

    moved = worker._move_single_path(source, target)

    assert moved == target
    assert len(attempts) == 2, "the move is retried exactly once"
    assert released == [True], "handles are released before the retry"


def test_other_errors_are_not_retried(
    worker: MoveWorker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"x")
    target = tmp_path / "dest" / "a.jpg"
    target.parent.mkdir()

    released: list[bool] = []
    monkeypatch.setattr(
        move_worker_module, "release_metadata_handles", lambda: released.append(True)
    )

    def failing_move(src, dst):
        raise OSError(errno.ENOSPC, "no space left")

    monkeypatch.setattr(move_worker_module.shutil, "move", failing_move)

    with pytest.raises(OSError):
        worker._move_single_path(source, target)

    assert released == [], "unrelated errors must not touch the metadata session"
