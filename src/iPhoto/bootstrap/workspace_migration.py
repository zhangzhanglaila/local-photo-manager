"""One-off migration of a nested work directory.

The workspace used to keep its files one level deeper (``<workspace>/<library>/.iPhoto``).
Reading the path used to run this migration as a side effect, so simply asking for the
workspace directory could rewrite the disk. It now runs explicitly at startup instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

WORK_DIR_NAME = ".iPhoto"


def migrate_nested_work_dir(workspace_dir: Path, *, nested_name: str = WORK_DIR_NAME) -> bool:
    """Move ``<workspace_dir>/<nested>/*`` up into ``workspace_dir``.

    Returns True when something was moved. Existing targets win (never overwritten),
    and the nested directory is only removed once it is empty — this migration never
    deletes data.
    """

    nested = workspace_dir / nested_name
    if not nested.is_dir():
        return False
    try:
        entries = list(nested.iterdir())
    except OSError:
        _LOGGER.warning("could not inspect %s", nested, exc_info=True)
        return False

    moved = False
    for entry in entries:
        target = workspace_dir / entry.name
        if target.exists():
            continue
        try:
            entry.rename(target)
            moved = True
        except OSError:
            _LOGGER.warning("could not move %s to %s", entry, target, exc_info=True)
            return moved

    try:
        nested.rmdir()
    except OSError:
        pass  # left behind when something could not be moved; never delete data
    return moved
