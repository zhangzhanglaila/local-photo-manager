"""The nested work-directory migration moves files without deleting data."""

from __future__ import annotations

from pathlib import Path

from iPhoto.bootstrap.workspace_migration import migrate_nested_work_dir


def _nested(workspace: Path) -> Path:
    nested = workspace / ".iPhoto"
    nested.mkdir(parents=True)
    return nested


def test_files_move_up_one_level(tmp_path: Path) -> None:
    nested = _nested(tmp_path)
    (nested / "global_index.db").write_text("db", encoding="utf-8")
    (nested / "thumbs").mkdir()

    assert migrate_nested_work_dir(tmp_path) is True

    assert (tmp_path / "global_index.db").read_text(encoding="utf-8") == "db"
    assert (tmp_path / "thumbs").is_dir()
    assert not nested.exists(), "an emptied nested directory is removed"


def test_existing_targets_are_never_overwritten(tmp_path: Path) -> None:
    nested = _nested(tmp_path)
    (nested / "links.json").write_text("old", encoding="utf-8")
    (tmp_path / "links.json").write_text("new", encoding="utf-8")

    migrate_nested_work_dir(tmp_path)

    assert (tmp_path / "links.json").read_text(encoding="utf-8") == "new"
    assert (nested / "links.json").exists(), "the conflicting file stays put"


def test_no_nested_directory_is_a_no_op(tmp_path: Path) -> None:
    assert migrate_nested_work_dir(tmp_path) is False


def test_non_empty_leftovers_are_kept(tmp_path: Path) -> None:
    nested = _nested(tmp_path)
    (nested / "keep.bin").write_text("x", encoding="utf-8")
    (tmp_path / "keep.bin").write_text("already here", encoding="utf-8")

    migrate_nested_work_dir(tmp_path)

    assert nested.is_dir(), "never delete data that could not be moved"
