"""Long-press preview must not double-rotate 180° clips.

``QGraphicsVideoItem`` applies the container display matrix, while the Qt6 FFmpeg
backend on Windows/Linux already delivers upright frames for 180° clips. The
detail player compensates for that; the plain preview path did not, so some Live
Photos played upside down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.gui.ui.widgets import preview_window as preview_module


@pytest.mark.parametrize(
    ("rotation_cw", "expected"),
    [
        (180, True),
        (0, False),
        (90, False),
        (270, False),
    ],
)
def test_rotation_safe_routing(
    monkeypatch: pytest.MonkeyPatch, rotation_cw: int, expected: bool
) -> None:
    monkeypatch.setattr(
        preview_module,
        "probe_video_rotation",
        lambda _source: (rotation_cw, 1920, 1080),
    )

    assert (
        preview_module._preview_needs_rotation_safe_surface(Path("clip.mov"))
        is expected
    )


def test_probe_failure_falls_back_to_the_plain_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_source):
        raise RuntimeError("ffprobe missing")

    monkeypatch.setattr(preview_module, "probe_video_rotation", _boom)

    assert preview_module._preview_needs_rotation_safe_surface(Path("clip.mov")) is False
