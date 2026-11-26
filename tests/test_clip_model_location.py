"""Search-model location must work in a checkout, a packaged build and via overrides.

The path used to be a hardcoded ``parents[4]``, which resolves *above* the install
directory once bundled. Resolution now goes through ``search_models.model_dir`` /
``model_paths``, with ``IPHOTO_MODELS_DIR`` (all packs) and
``IPHOTO_CLIP_MODEL_DIR`` (search only) as explicit overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.agent.services import search_models


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("IPHOTO_MODELS_DIR", raising=False)
    monkeypatch.delenv("IPHOTO_CLIP_MODEL_DIR", raising=False)


def test_clip_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("IPHOTO_CLIP_MODEL_DIR", str(target))
    spec = search_models.resolve_spec("clip-b32")

    assert search_models.model_dir(spec) == target / spec.dir_name


def test_models_dir_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "all-models"
    monkeypatch.setenv("IPHOTO_MODELS_DIR", str(target))

    assert search_models.models_root() == target


def test_checkout_resolves_to_the_repository_root() -> None:
    resolved = search_models.models_root()

    # The canonical root is <repo>/extension/models — not src/extension/models,
    # which is only read for compatibility with older installs.
    assert resolved.name == "models"
    assert resolved.parent.name == "extension"
    assert resolved.parent.parent.name != "src"


def test_resolution_is_stable_without_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IPHOTO_MODELS_DIR", raising=False)

    first = search_models.models_root()
    second = search_models.models_root()

    assert first == second
