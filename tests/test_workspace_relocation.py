"""Relocating a workspace copies the data and never removes the original."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.bootstrap.workspace_relocation import RelocationError, relocate_workspace


def _workspace(root: Path) -> Path:
    work = root / "APPLE"
    (work / "thumbs").mkdir(parents=True)
    (work / "global_index.db").write_text("index", encoding="utf-8")
    (work / "thumbs" / "a.jpg").write_bytes(b"x" * 32)
    return work


def test_copy_moves_every_file_and_keeps_the_source(tmp_path: Path) -> None:
    source = _workspace(tmp_path / "current")
    destination = tmp_path / "new" / "APPLE"

    result = relocate_workspace(source, destination)

    assert result.destination == destination
    assert (destination / "global_index.db").read_text(encoding="utf-8") == "index"
    assert (destination / "thumbs" / "a.jpg").exists()
    assert result.copied_bytes >= 37
    assert (source / "global_index.db").exists(), "the original must survive"


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RelocationError):
        relocate_workspace(tmp_path / "absent", tmp_path / "new")


def test_destination_inside_the_source_is_rejected(tmp_path: Path) -> None:
    source = _workspace(tmp_path / "current")

    with pytest.raises(RelocationError):
        relocate_workspace(source, source / "copy")


def test_non_empty_destination_is_rejected_unless_overwriting(tmp_path: Path) -> None:
    source = _workspace(tmp_path / "current")
    destination = tmp_path / "used"
    destination.mkdir()
    (destination / "keep.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(RelocationError):
        relocate_workspace(source, destination)

    result = relocate_workspace(source, destination, overwrite=True)
    assert result.already_existed is True
    assert (destination / "global_index.db").exists()
