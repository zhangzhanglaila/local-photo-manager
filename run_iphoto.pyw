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

    # The splash must animate while the heavy modules (Qt plugins, torch, ONNX) import.
    # A blocking import freezes the event loop, which looked like a stuck progress bar,
    # so the import runs on a worker thread while this thread keeps pumping events.
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    splash = None
    try:
        from iPhoto.gui.ui.widgets.splash_screen import SplashScreen

        splash = SplashScreen()
        splash.show()
        splash.raise_()
        splash.activateWindow()
        app.processEvents()
    except Exception:  # noqa: BLE001 - never block startup on cosmetics
        splash = None

    import threading
    import time

    loaded: dict[str, object] = {}

    def _load() -> None:
        from iPhoto.gui.main import main as gui_main

        loaded["main"] = gui_main

    worker = threading.Thread(target=_load, name="iPhoto-import", daemon=True)
    worker.start()
    while worker.is_alive():
        app.processEvents()
        time.sleep(0.008)

    gui_main = loaded.get("main")
    if gui_main is None:  # pragma: no cover
        raise SystemExit(1)
    return gui_main(app=app, splash=splash)  # type: ignore[operator]


if __name__ == "__main__":
    raise SystemExit(main())
