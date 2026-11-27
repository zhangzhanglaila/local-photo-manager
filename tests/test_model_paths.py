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

def test_legacy_packs_are_moved_into_the_canonical_tree(trees):
    canonical, legacy = trees
    (legacy / "buffalo_s").mkdir()
    (legacy / "buffalo_s" / "det_500m.onnx").write_bytes(b"x")

    moved, skipped = model_paths.migrate_legacy_tree()

    assert moved == [(legacy / "buffalo_s", canonical / "buffalo_s")]
    assert skipped == []
    assert (canonical / "buffalo_s" / "det_500m.onnx").is_file()
    assert not (legacy / "buffalo_s").exists()


def test_migration_never_overwrites_a_canonical_pack(trees):
    canonical, legacy = trees
    (canonical / "buffalo_l").mkdir()
    (canonical / "buffalo_l" / "keep.onnx").write_bytes(b"new")
    (legacy / "buffalo_l").mkdir()
    (legacy / "buffalo_l" / "keep.onnx").write_bytes(b"old")

    moved, skipped = model_paths.migrate_legacy_tree()

    assert moved == []
    assert skipped == [legacy / "buffalo_l"]
    assert (canonical / "buffalo_l" / "keep.onnx").read_bytes() == b"new"


def _write_pack(root: Path, name: str, payload: bytes) -> Path:
    pack = root / name
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "det.onnx").write_bytes(payload)
    return pack


def test_identical_legacy_duplicates_are_reported_then_removed(trees):
    canonical, legacy = trees
    _write_pack(canonical, "buffalo_l", b"same")
    _write_pack(legacy, "buffalo_l", b"same")

    removed, kept, freed = model_paths.remove_identical_legacy_duplicates()

    assert removed == [(legacy / "buffalo_l", canonical / "buffalo_l")]
    assert kept == []
    assert freed == len(b"same")
    assert (legacy / "buffalo_l").is_dir(), "a dry run must not delete anything"

    removed, kept, freed = model_paths.remove_identical_legacy_duplicates(apply=True)

    assert removed == [(legacy / "buffalo_l", canonical / "buffalo_l")]
    assert kept == []
    assert not (legacy / "buffalo_l").exists()
    # The emptied legacy root is pruned as well; the canonical copy stays.
    assert not legacy.exists()
    assert (canonical / "buffalo_l" / "det.onnx").read_bytes() == b"same"


def test_differing_legacy_pack_is_never_deleted(trees):
    canonical, legacy = trees
    _write_pack(canonical, "buffalo_l", b"newer")
    _write_pack(legacy, "buffalo_l", b"older")

    removed, kept, freed = model_paths.remove_identical_legacy_duplicates(apply=True)

    assert removed == []
    assert kept == [(legacy / "buffalo_l", canonical / "buffalo_l")]
    assert freed == 0
    assert (legacy / "buffalo_l" / "det.onnx").read_bytes() == b"older"
    assert (canonical / "buffalo_l" / "det.onnx").read_bytes() == b"newer"


def test_unmatched_legacy_pack_is_left_for_migration(trees):
    _canonical, legacy = trees
    _write_pack(legacy, "buffalo_s", b"only-here")

    removed, kept, freed = model_paths.remove_identical_legacy_duplicates(apply=True)

    assert (removed, kept, freed) == ([], [], 0)
    assert (legacy / "buffalo_s" / "det.onnx").read_bytes() == b"only-here"

