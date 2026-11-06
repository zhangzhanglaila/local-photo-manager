"""One definition of "these two paths are the same file".

Fifteen modules carried their own copy, and they disagreed exactly where it
matters: when ``resolve()`` raises (8.3 short names, symlinks, trailing
separators) some returned ``False`` while others compared the raw paths. That
made move/delete/dedupe decisions depend on which copy ran.
"""

from __future__ import annotations

from pathlib import Path

from iPhoto.utils.pathutils import paths_equal


def test_same_path_is_equal() -> None:
    assert paths_equal(Path("/library/Trip/a.jpg"), Path("/library/Trip/a.jpg"))


def test_relative_and_absolute_paths_resolve_to_the_same_location(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"x")

    assert paths_equal(tmp_path / "a.jpg", Path(str(tmp_path) + "/a.jpg"))


def test_different_paths_are_not_equal(tmp_path: Path) -> None:
    assert not paths_equal(tmp_path / "a.jpg", tmp_path / "b.jpg")


def test_none_is_never_equal() -> None:
    assert not paths_equal(None, Path("/library/a.jpg"))
    assert not paths_equal(Path("/library/a.jpg"), None)
    assert not paths_equal(None, None)


def test_strings_are_accepted() -> None:
    assert paths_equal("/library/a.jpg", "/library/./a.jpg")
