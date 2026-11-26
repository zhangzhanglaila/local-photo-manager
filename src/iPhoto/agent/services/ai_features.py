"""User-facing switches for the two local AI scans: faces and text in photos.

Both scans run automatically after a library scan. Until now they could only be
turned off with an environment variable (``IPHOTO_SKIP_FACE_SCAN``), which is not
something a user can find; the switches live in settings under ``agent.*``.

The scan coordinator is constructed before the settings window exists, so the
provider is registered once at startup instead of threading a settings object
through every constructor.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

#: Settings keys (``agent.*`` namespace, shared with the semantic-search switch).
FACE_KEY = "agent.face_recognition_enabled"
OCR_KEY = "agent.ocr_enabled"
FACE_REBUILD_KEY = "agent.face.rebuild_request"
OCR_REBUILD_KEY = "agent.ocr.rebuild_request"

_provider: Callable[[], Any] | None = None


def set_settings_provider(provider: Callable[[], Any] | None) -> None:
    """Register the callable that returns the live settings manager."""

    global _provider
    _provider = provider


def _settings() -> Any | None:
    if _provider is None:
        return None
    try:
        return _provider()
    except Exception:  # pragma: no cover - the provider is set at startup
        _LOGGER.debug("settings provider failed", exc_info=True)
        return None


def _read(key: str, default: bool) -> bool:
    settings = _settings()
    if settings is None:
        return default
    try:
        value = settings.get(key, default)
    except Exception:  # pragma: no cover - defensive
        return default
    return bool(value) if isinstance(value, bool) else default


def face_enabled() -> bool:
    """Whether the face scan should run after a library scan."""

    return _read(FACE_KEY, True)


def ocr_enabled() -> bool:
    """Whether the text-in-photos (OCR) scan should run."""

    return _read(OCR_KEY, True)


def set_face_enabled(enabled: bool) -> None:
    settings = _settings()
    if settings is not None:
        settings.set(FACE_KEY, bool(enabled))


def set_ocr_enabled(enabled: bool) -> None:
    settings = _settings()
    if settings is not None:
        settings.set(OCR_KEY, bool(enabled))


__all__ = [
    "FACE_KEY",
    "FACE_REBUILD_KEY",
    "OCR_KEY",
    "OCR_REBUILD_KEY",
    "face_enabled",
    "ocr_enabled",
    "set_face_enabled",
    "set_ocr_enabled",
    "set_settings_provider",
]
