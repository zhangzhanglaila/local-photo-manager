"""Album work files belong to the library workspace, not to a folder per album."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.utils import pathutils


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", None)
    monkeypatch.setattr(pathutils, "_KNOWN_LIBRARY_ROOTS", ())
    yield
    pathutils.set_known_library_roots(None)


def test_album_path_uses_its_library_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    library = tmp_path / "APPLE"
    album = library / "197006_a"
    album.mkdir(parents=True)
    workspace.mkdir()

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", workspace)
    pathutils.set_known_library_roots([library])

    expected = workspace / "APPLE" / "albums" / "197006_a"
    assert pathutils.get_custom_workspace_dir(album) == expected
    assert pathutils.ensure_work_dir(album) == expected
    assert not (workspace / "197006_a").exists()


def test_unknown_folder_keeps_its_own_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    library = tmp_path / "APPLE"
    loose = tmp_path / "loose_album"
    library.mkdir()
    loose.mkdir()
    workspace.mkdir()

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", workspace)
    pathutils.set_known_library_roots([library])

    assert pathutils.ensure_work_dir(loose) == workspace / "loose_album"


def test_nested_roots_use_the_closest_root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outer = tmp_path / "outer"
    inner = outer / "inner"
    album = inner / "album"
    album.mkdir(parents=True)
    workspace.mkdir()

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", workspace)
    pathutils.set_known_library_roots([outer, inner])

    assert pathutils.workspace_owner(album) == inner
    assert pathutils.ensure_work_dir(album) == workspace / "inner" / "albums" / "album"


def test_two_albums_never_share_a_workspace_file(tmp_path, monkeypatch):
    """Regression: every album resolved to the library workspace, so all of them
    read the library's manifest and showed its title."""

    workspace = tmp_path / "workspace"
    library = tmp_path / "APPLE"
    first = library / "197006_a"
    second = library / "202311__"
    first.mkdir(parents=True)
    second.mkdir()
    workspace.mkdir()

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", workspace)
    pathutils.set_known_library_roots([library])

    assert pathutils.album_manifest_path(first) == (
        workspace / "APPLE" / "albums" / "197006_a" / "manifest.json"
    )
    assert pathutils.album_manifest_path(second) == (
        workspace / "APPLE" / "albums" / "202311__" / "manifest.json"
    )
    assert pathutils.album_manifest_path(library) == workspace / "APPLE" / "manifest.json"
