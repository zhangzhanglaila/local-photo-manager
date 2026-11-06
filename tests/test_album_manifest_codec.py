"""One manifest schema, one default, one normalisation rule.

Five copies of "read, repair, write back" had drifted: every one forced ``title``
back to the directory name, so a custom album name was overwritten on the next
read. The codec keeps a valid title and only falls back to the folder name when it
is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.domain.services.album_manifest import (
    ALBUM_MANIFEST_SCHEMA,
    default_album_manifest,
    normalise_album_manifest,
)


def test_default_manifest_is_complete() -> None:
    manifest = default_album_manifest(Path("/library/Trip"))

    assert manifest["schema"] == ALBUM_MANIFEST_SCHEMA
    assert manifest["title"] == "Trip"
    assert manifest["filters"] == {}
    assert manifest["id"]


def test_custom_title_survives_normalisation() -> None:
    manifest = {
        "schema": ALBUM_MANIFEST_SCHEMA,
        "id": "abc",
        "title": "Our Summer",
        "filters": {},
    }

    changed = normalise_album_manifest(Path("/library/2024_trip"), manifest)

    assert changed is False
    assert manifest["title"] == "Our Summer", "a user-chosen title must not be replaced"


def test_missing_title_falls_back_to_the_folder_name() -> None:
    manifest = {"schema": ALBUM_MANIFEST_SCHEMA, "id": "abc", "filters": {}}

    changed = normalise_album_manifest(Path("/library/Trip"), manifest)

    assert changed is True
    assert manifest["title"] == "Trip"


def test_schema_and_id_are_repaired() -> None:
    manifest = {"title": "Trip"}

    changed = normalise_album_manifest(Path("/library/Trip"), manifest)

    assert changed is True
    assert manifest["schema"] == ALBUM_MANIFEST_SCHEMA
    assert manifest["id"]
    assert manifest["filters"] == {}


def test_blank_title_is_replaced() -> None:
    manifest = {"schema": ALBUM_MANIFEST_SCHEMA, "id": "abc", "title": "   ", "filters": {}}

    assert normalise_album_manifest(Path("/library/Trip"), manifest) is True
    assert manifest["title"] == "Trip"


def test_rename_updates_the_manifest_title(tmp_path: Path) -> None:
    """A directory rename must update the title explicitly now."""

    pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")
    from PySide6.QtWidgets import QApplication

    from iPhoto.library.runtime_controller import LibraryRuntimeController
    from iPhoto.utils.jsonio import read_json
    from iPhoto.utils.pathutils import find_album_manifest

    QApplication.instance() or QApplication([])
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    try:
        album = manager.create_album("Departure")
        manager.rename_album(album, "Arrival")

        manifest_path = find_album_manifest(root / "Arrival")
        assert manifest_path is not None
        assert read_json(manifest_path)["title"] == "Arrival"
    finally:
        manager.shutdown()
