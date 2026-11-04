"""The album manifest must live in the configured workspace.

`docs/custom_workspace.md` promises that the photo folder stays clean and holds
only photos and videos, but the manifest was always written as
``<library>/.iphoto.album.json`` regardless of where the workspace lived. That is
how a state file ended up next to the photos in a library that had a workspace
configured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iPhoto.application.services.album_manifest_service import _save_manifest
from iPhoto.utils.pathutils import (
    album_manifest_path,
    find_album_manifest,
    set_custom_workspace_base,
)


@pytest.fixture(autouse=True)
def _reset_workspace() -> None:
    set_custom_workspace_base(None)
    yield
    set_custom_workspace_base(None)


def _library(tmp_path: Path) -> Path:
    library = tmp_path / "APPLE"
    library.mkdir()
    (library / "202401").mkdir()
    (library / "202401" / "IMG_1.jpg").write_bytes(b"photo")
    return library


def _manifest(album_id: str = "album-1") -> dict:
    return {
        "schema": "iPhoto/album@1",
        "id": album_id,
        "title": "APPLE",
        "filters": {},
    }


def test_manifest_stays_in_the_album_without_a_workspace(tmp_path: Path) -> None:
    library = _library(tmp_path)

    assert album_manifest_path(library) == library / ".iphoto.album.json"
    assert find_album_manifest(library) is None


def test_manifest_moves_into_a_configured_workspace(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = tmp_path / "Workspace"
    set_custom_workspace_base(workspace)

    expected = workspace / "APPLE" / ".iPhoto" / "manifest.json"
    assert album_manifest_path(library) == expected


def test_writing_the_manifest_keeps_the_library_clean(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = tmp_path / "Workspace"
    set_custom_workspace_base(workspace)

    _save_manifest(library, _manifest())

    workspace_manifest = workspace / "APPLE" / ".iPhoto" / "manifest.json"
    assert workspace_manifest.exists(), "the manifest belongs in the workspace"
    assert json.loads(workspace_manifest.read_text(encoding="utf-8"))["id"] == "album-1"

    leftovers = [p.name for p in library.iterdir() if p.is_file()]
    assert leftovers == [], f"the photo folder must stay clean, found {leftovers}"


def test_existing_manifest_is_migrated_into_the_workspace(tmp_path: Path) -> None:
    library = _library(tmp_path)
    legacy = library / ".iphoto.album.json"
    legacy.write_text(json.dumps(_manifest("legacy-id")), encoding="utf-8")

    workspace = tmp_path / "Workspace"
    set_custom_workspace_base(workspace)

    resolved = find_album_manifest(library)

    assert resolved is not None
    assert resolved == workspace / "APPLE" / ".iPhoto" / "manifest.json"
    assert resolved.exists()
    assert json.loads(resolved.read_text(encoding="utf-8"))["id"] == "legacy-id"
    assert not legacy.exists(), "the legacy manifest must not stay in the photo folder"
