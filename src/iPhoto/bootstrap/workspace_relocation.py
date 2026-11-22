"""Move a library's workspace to a new location without risking the data.

The workspace holds the index database, thumbnails, face crops and embeddings — the
expensive, regenerable-but-slow parts of the app. Relocation therefore *copies* first,
verifies, and only then lets the caller switch: the old directory is never deleted by
this module, so a failure at any point leaves the user with a working installation.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelocationResult:
    """Outcome of a workspace relocation attempt."""

    destination: Path
    copied_bytes: int
    already_existed: bool


class RelocationError(RuntimeError):
    """Raised when the workspace could not be copied to the destination."""


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def relocate_workspace(source: Path, destination: Path, *, overwrite: bool = False) -> RelocationResult:
    """Copy the workspace at *source* to *destination*.

    Raises :class:`RelocationError` when the destination is unusable or the copy fails;
    the source directory is left untouched in every case.
    """

    source = Path(source)
    destination = Path(destination)

    if not source.is_dir():
        raise RelocationError(f"workspace does not exist: {source}")
    if destination == source:
        raise RelocationError("destination is the current workspace")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise RelocationError("destination is inside the current workspace")

    if destination.exists():
        if not overwrite and any(destination.iterdir()):
            raise RelocationError(f"destination is not empty: {destination}")
        already_existed = True
    else:
        already_existed = False

    try:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)
    except OSError as exc:
        raise RelocationError(f"copy failed: {exc}") from exc

    copied = _directory_size(destination)
    expected = _directory_size(source)
    if copied < expected:
        raise RelocationError(
            f"copy is incomplete: {copied} of {expected} bytes; the original was kept"
        )

    _LOGGER.info(
        "workspace relocated: %s -> %s (%d bytes)", source, destination, copied
    )
    return RelocationResult(
        destination=destination, copied_bytes=copied, already_existed=already_existed
    )
