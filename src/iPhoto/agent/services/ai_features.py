"""Settings keys and defaults for the two local AI scans: faces and text.

Both scans run automatically after a library scan. Until now they could only be
turned off with an environment variable (``IPHOTO_SKIP_FACE_SCAN``), which is not
something a user can find; the switches live in settings under ``agent.*``.

The readers/writers for these keys live in ``bootstrap`` (see
``SettingsAiFeatureSwitches``): the scan coordinator receives them as a
dependency instead of reaching for a module-level settings provider.
"""

from __future__ import annotations

#: Settings keys (``agent.*`` namespace, shared with the semantic-search switch).
FACE_KEY = "agent.face_recognition_enabled"
OCR_KEY = "agent.ocr_enabled"
FACE_REBUILD_KEY = "agent.face.rebuild_request"
OCR_REBUILD_KEY = "agent.ocr.rebuild_request"

__all__ = [
    "FACE_KEY",
    "FACE_REBUILD_KEY",
    "OCR_KEY",
    "OCR_REBUILD_KEY",
]
