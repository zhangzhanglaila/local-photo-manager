"""Face/OCR switches are injected instead of registered through a global provider."""

from __future__ import annotations

from iPhoto.agent.services import ai_features
from iPhoto.library.runtime_controller import LibraryRuntimeController


def test_module_no_longer_exposes_a_settings_provider() -> None:
    assert not hasattr(ai_features, "set_settings_provider")


def test_scan_gating_reads_the_injected_switches() -> None:
    class _Switches:
        def face_enabled(self) -> bool:
            return False

        def ocr_enabled(self) -> bool:
            return True

    controller = LibraryRuntimeController.__new__(LibraryRuntimeController)
    controller._feature_switches = _Switches()

    assert controller._ai_face_enabled() is False
    assert controller._ai_ocr_enabled() is True


def test_missing_switches_keep_both_scans_on() -> None:
    controller = LibraryRuntimeController.__new__(LibraryRuntimeController)

    assert controller._ai_face_enabled() is True
    assert controller._ai_ocr_enabled() is True
