"""Windowed launcher for the desktop shortcut.

``pythonw.exe`` starts without stdout/stderr, and parts of the application (and Qt's
own diagnostics) write to those streams; the process then exits before a window appears,
which looks like "the shortcut does nothing".

This launcher guarantees both streams exist by pointing them at a log file next to the
application log, then hands over to the normal entry point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / "iPhoto"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_streams() -> None:
    log_dir = _log_dir()
    if sys.stdout is None:
        sys.stdout = open(log_dir / "launcher.out", "w", buffering=1, encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(log_dir / "launcher.err", "w", buffering=1, encoding="utf-8")


def main() -> int:
    _ensure_streams()
    from iPhoto.gui.main import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
