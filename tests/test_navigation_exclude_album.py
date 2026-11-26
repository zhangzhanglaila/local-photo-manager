"""Removing an album folder must write its exclude pattern to the root manifest.

The album branch of ``_handle_exclude_album`` had been left in
``_update_checked_album_paths_after_removal``, where ``owning_root``/``path`` did
not exist: excluding an album did nothing, and removing a library root raised
``NameError`` while other roots remained.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator


class _Library:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.scanned = 0

    def root(self) -> Path:
        return self._root

    def roots(self) -> list[Path]:
        return [self._root]

    def scan_tree(self) -> None:
        self.scanned += 1


class _Settings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def set(self, key, value) -> None:
        self.values[key] = value


class _Facade:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def scan_root_async(self, root, *, include, exclude) -> None:
        self.calls.append({"root": root, "include": list(include), "exclude": list(exclude)})


class _Gallery:
    def __init__(self) -> None:
        self.checked = "unset"

    def set_checked_album_paths(self, paths) -> None:
        self.checked = paths


def _coordinator(root: Path) -> NavigationCoordinator:
    coordinator = NavigationCoordinator.__new__(NavigationCoordinator)
    coordinator._context = SimpleNamespace(library=_Library(root), settings=_Settings())
    coordinator._facade = _Facade()
    coordinator._gallery_vm = _Gallery()
    coordinator._sidebar = None
    coordinator._grid_view = None
    coordinator._logger = __import__("logging").getLogger("test")
    coordinator._router = SimpleNamespace()
    return coordinator


@pytest.fixture()
def library(tmp_path: Path, monkeypatch) -> Path:
    from iPhoto.utils import pathutils

    monkeypatch.setattr(pathutils, "set_custom_workspace_base", lambda *a, **k: None)
    album = tmp_path / "2024_trip"
    album.mkdir()
    return tmp_path


def test_excluding_an_album_writes_the_manifest_pattern(library: Path) -> None:
    from iPhoto.utils.jsonio import read_json
    from iPhoto.utils.pathutils import album_manifest_path, find_album_manifest

    coordinator = _coordinator(library)

    coordinator._handle_exclude_album(library / "2024_trip")

    manifest_path = find_album_manifest(library) or album_manifest_path(library)
    manifest = read_json(manifest_path)
    assert "2024_trip/**" in manifest["filters"]["exclude"]
    # The rescan is what actually drops the folder from the index.
    assert coordinator._facade.calls, "the folder removal must trigger a rescan"
    assert "2024_trip/**" in coordinator._facade.calls[-1]["exclude"]
    assert coordinator._context.library.scanned == 1


def test_excluding_the_same_album_twice_does_not_duplicate_the_pattern(library: Path) -> None:
    from iPhoto.utils.jsonio import read_json
    from iPhoto.utils.pathutils import album_manifest_path, find_album_manifest

    coordinator = _coordinator(library)
    coordinator._handle_exclude_album(library / "2024_trip")
    coordinator._handle_exclude_album(library / "2024_trip")

    manifest_path = find_album_manifest(library) or album_manifest_path(library)
    exclude = read_json(manifest_path)["filters"]["exclude"]
    assert exclude.count("2024_trip/**") == 1


def test_removing_a_root_while_others_remain_does_not_raise(library: Path) -> None:
    """This used to read names that did not exist in scope and raise NameError."""

    coordinator = _coordinator(library)

    coordinator._update_checked_album_paths_after_removal(library / "old_root")

    # Checked paths follow the surviving roots, and the setting is kept in sync.
    assert coordinator._gallery_vm.checked == [library]
    assert coordinator._context.settings.values["ui.checked_library_paths"] == [str(library)]
