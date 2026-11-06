"""Logging helpers for iPhoto."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from typing import Optional

_LOGGER: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """Return a module-level logger configured for iPhoto."""

    global _LOGGER
    if _LOGGER is None:
        _LOGGER = logging.getLogger("iPhoto")
        if not _LOGGER.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            handler.setFormatter(formatter)
            _LOGGER.addHandler(handler)
            log_path = _default_log_path()
            if log_path is not None:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    file_handler = RotatingFileHandler(
                        log_path,
                        maxBytes=2_000_000,
                        backupCount=3,
                        encoding="utf-8",
                    )
                    file_handler.setFormatter(formatter)
                    _LOGGER.addHandler(file_handler)
                except OSError:
                    pass
        _LOGGER.setLevel(logging.INFO)
    return _LOGGER


def _default_log_path() -> Path | None:
    override = os.environ.get("IPHOTO_LOG_DIR")
    if override:
        return Path(override).expanduser() / "iPhoto.log"
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base).expanduser() / "iPhoto" / "iPhoto.log"


logger = get_logger()


#: Development-only diagnostics are off unless this is set (or DEBUG is enabled).
DIAG_ENV = "IPHOTO_DIAG"


def diag_enabled() -> bool:
    """Return whether temporary diagnostics should be emitted."""

    raw = os.environ.get(DIAG_ENV, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    logger = get_logger()
    return logger.isEnabledFor(logging.DEBUG)


def diag(message: str, *args: object, level: int = logging.INFO) -> None:
    """Log *message* only while diagnostics are enabled.

    Debug instrumentation used to be written unconditionally at INFO (and, for the
    video trim path, at WARNING), so a normal session filled the log with internal
    step markers and buried real warnings. Routing it through this helper keeps the
    instrumentation available while making it opt-in.
    """

    if not diag_enabled():
        return
    get_logger().log(level, message, *args)
