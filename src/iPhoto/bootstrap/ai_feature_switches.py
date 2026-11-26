"""Settings-backed implementation of the local-AI feature switches.

``LibraryRuntimeController`` decides after every scan whether the face and OCR
workers should run. It used to ask a module-level provider that ``bootstrap``
registered at startup; the switches are now a normal injected object, so a test
or a second window can point them at its own settings.
"""

from __future__ import annotations

from typing import Any

from ..agent.services.ai_features import FACE_KEY, OCR_KEY


class SettingsAiFeatureSwitches:
    """Read and write the face/OCR switches in the live settings manager."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def face_enabled(self) -> bool:
        return self._read(FACE_KEY)

    def ocr_enabled(self) -> bool:
        return self._read(OCR_KEY)

    def set_face_enabled(self, enabled: bool) -> None:
        self._settings.set(FACE_KEY, bool(enabled))

    def set_ocr_enabled(self, enabled: bool) -> None:
        self._settings.set(OCR_KEY, bool(enabled))

    def _read(self, key: str) -> bool:
        try:
            value = self._settings.get(key, True)
        except Exception:  # pragma: no cover - defensive
            return True
        return value if isinstance(value, bool) else True


__all__ = ["SettingsAiFeatureSwitches"]
