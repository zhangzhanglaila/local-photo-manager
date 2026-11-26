"""Trim thumbnail count comes from recorded geometry, never a live ffprobe."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iPhoto.gui.coordinators import edit_coordinator


def test_count_scales_with_the_visible_strip() -> None:
    # 16:9 landscape → thumbnails are ~74 px wide at the 42 px height.
    assert edit_coordinator._video_trim_thumbnail_count(1000, 0, 1920, 1080) == 16
    # Rotated 90°: display width/height swap, so more, narrower thumbnails.
    assert edit_coordinator._video_trim_thumbnail_count(1000, 90, 1920, 1080) == 45
    assert edit_coordinator._video_trim_thumbnail_count(1000, 270, 1920, 1080) == 45


def test_count_is_clamped_and_handles_unknown_geometry() -> None:
    assert edit_coordinator._video_trim_thumbnail_count(1000, 0, 0, 0) == 10
    assert edit_coordinator._video_trim_thumbnail_count(60, 0, 0, 0) == 6
    assert edit_coordinator._video_trim_thumbnail_count(10000, 0, 1920, 1080) == 60


def _fail_probe(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the trim estimator must not run ffprobe")


def test_estimator_uses_recorded_rotation_without_probing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(edit_coordinator, "probe_media", _fail_probe)
    source = tmp_path / "clip.mov"
    self_ = SimpleNamespace(
        _ui=SimpleNamespace(
            video_trim_bar=SimpleNamespace(thumbnail_view_width=lambda: 1000)
        ),
        _video_rotation_source=source,
        _video_rotation_info=(90, 1920, 1080),
    )

    width, count = edit_coordinator.EditCoordinator._estimate_video_trim_thumbnail_request(
        self_, source
    )

    assert (width, count) == (1000, 45)


def test_estimator_falls_back_without_probing_when_geometry_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(edit_coordinator, "probe_media", _fail_probe)
    self_ = SimpleNamespace(
        _ui=SimpleNamespace(
            video_trim_bar=SimpleNamespace(thumbnail_view_width=lambda: 700)
        ),
        _video_rotation_source=tmp_path / "other.mov",
        _video_rotation_info=(90, 1920, 1080),
    )

    width, count = edit_coordinator.EditCoordinator._estimate_video_trim_thumbnail_request(
        self_, tmp_path / "clip.mov"
    )

    assert (width, count) == (700, 7)
