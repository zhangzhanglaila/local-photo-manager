"""CLIP model location must work in a source checkout and in a standalone build.

The path used to be a hardcoded ``parents[4]``, which resolves *above* the install
directory once bundled, so a packaged app could never find the model. Resolution now
follows the same rule as the face models and the map extension: explicit override,
then the packaged location, then the checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from iPhoto.agent.infrastructure.clip_downloader import get_model_dir


def test_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("IPHOTO_CLIP_MODEL_DIR", str(target))

    assert get_model_dir(tmp_path) == target


def test_checkout_resolves_to_the_repository_root() -> None:
    resolved = get_model_dir(Path("."))

    # The module lives under src/iPhoto/agent/infrastructure, so the repository root is
    # four levels up — not ``src`` (which only holds the face models).
    assert resolved.name == "models"
    assert resolved.parent.name == "extension"
    assert (resolved / "clip-vit-base-patch32").exists() or not resolved.exists()


def test_resolution_is_stable_without_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPHOTO_CLIP_MODEL_DIR", raising=False)

    first = get_model_dir(tmp_path)
    second = get_model_dir(tmp_path)

    assert first == second
    assert os.environ.get("IPHOTO_CLIP_MODEL_DIR") is None
