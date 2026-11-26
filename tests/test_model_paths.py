"""One answer to "where do the AI models live?"

The three registries used different `parents[N]` arithmetic, so the same machine
ended up with two `extension/models` trees and a pack placed in the documented
folder was reported as missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.agent.services import model_paths


@pytest.fixture()
def trees(tmp_path, monkeypatch):
    canonical = tmp_path / "extension" / "models"
    legacy = tmp_path / "src" / "extension" / "models"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    monkeypatch.setattr(model_paths, "install_root", lambda: canonical)
    monkeypatch.setattr(model_paths, "legacy_roots", lambda: (legacy,))
    monkeypatch.setattr(model_paths, "_LEGACY_WARNED", False)
    return canonical, legacy


def test_the_canonical_root_is_the_documented_one():
    root = model_paths.install_root()

    assert root.name == "models"
    assert root.parent.name == "extension"
    # <repo>/extension/models, not <repo>/src/extension/models
    assert root.parent.parent.name != "src"


def test_resolution_prefers_an_existing_download(trees):
    canonical, legacy = trees
    (legacy / "buffalo_l").mkdir()
    (legacy / "buffalo_l" / "det_10g.onnx").write_bytes(b"x")

    resolved = model_paths.resolve_dir("buffalo_l", patterns=("*.onnx",))

    assert resolved == legacy / "buffalo_l"


def test_a_missing_pack_defaults_to_the_canonical_root(trees):
    canonical, _legacy = trees

    assert model_paths.resolve_dir("siglip2-base") == canonical / "siglip2-base"


def test_the_legacy_tree_is_reported_once(trees, caplog):
    _canonical, legacy = trees
    (legacy / "pack").mkdir()
    (legacy / "pack" / "config.json").write_text("{}", encoding="utf-8")

    with caplog.at_level("WARNING"):
        model_paths.resolve_dir("pack")
        model_paths.resolve_dir("pack")

    warnings = [record for record in caplog.records if "legacy location" in record.message]
    assert len(warnings) == 1


def test_directories_are_discovered_in_both_trees(trees):
    canonical, legacy = trees
    (canonical / "ocr-new").mkdir()
    (legacy / "ocr-old").mkdir()

    found = model_paths.existing_dirs("ocr-*")

    assert set(found) == {canonical / "ocr-new", legacy / "ocr-old"}


def test_all_registries_agree_on_the_root(trees):
    """The regression this module exists for: three registries, one answer."""

    from iPhoto.agent.services import face_models, ocr_models, search_models

    canonical, _legacy = trees

    assert search_models.models_root() == canonical
    assert face_models.models_root() == canonical
    assert ocr_models.models_root() == canonical
