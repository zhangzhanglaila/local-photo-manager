"""Library-wide events: album rename, People snapshot, move completion toast."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.gui.coordinators.library_events_coordinator import LibraryEventsCoordinator


class _Pinned:
    def __init__(self) -> None:
        self.remapped: list[tuple] = []
        self.pruned: list[tuple] = []

    def remap_album_path(self, old, new, *, library_root, fallback_label) -> None:
        self.remapped.append((old, new, library_root, fallback_label))

    def prune_missing_people_entities(self, root, **kwargs) -> None:
        self.pruned.append((root, kwargs))


class _Thumbnails:
    def __init__(self) -> None:
        self.remapped: list[tuple] = []

    def remap_album_paths(self, old, new) -> None:
        self.remapped.append((old, new))


class _Gallery:
    def __init__(self) -> None:
        self.renamed: list[tuple] = []

    def handle_album_renamed(self, old, new) -> None:
        self.renamed.append((old, new))


class _Toast:
    def __init__(self) -> None:
        self.shown: list[str] = []

    def show_toast(self, message: str) -> None:
        self.shown.append(message)


class _Sidebar:
    def __init__(self) -> None:
        self.refreshed = 0

    def refresh_tree_model(self) -> None:
        self.refreshed += 1


def _make(library_root: Path | None = None, trash_root: Path | None = None):
    pinned = _Pinned()
    thumbnails = _Thumbnails()
    gallery = _Gallery()
    toast = _Toast()
    sidebar = _Sidebar()
    context = SimpleNamespace(
        library=SimpleNamespace(
            root=lambda: library_root,
            deleted_directory=lambda: trash_root,
        )
    )
    window = SimpleNamespace(
        ui=SimpleNamespace(
            notification_toast=toast,
            sidebar=sidebar,
        )
    )
    coordinator = LibraryEventsCoordinator(
        window,
        context,
        pinned_items_service=pinned,
        thumbnail_service=thumbnails,
        gallery_vm=gallery,
    )
    return coordinator, pinned, thumbnails, gallery, toast, sidebar


def test_album_rename_updates_pins_thumbnails_and_gallery(tmp_path: Path) -> None:
    old = tmp_path / "Old"
    new = tmp_path / "New"
    coordinator, pinned, thumbnails, gallery, _toast, _sidebar = _make(tmp_path / "Library")

    coordinator.on_album_renamed(old, new)

    assert pinned.remapped == [(old, new, tmp_path / "Library", "New")]
    assert thumbnails.remapped == [(old, new)]
    assert gallery.renamed == [(old, new)]


def test_people_snapshot_prunes_only_for_the_active_library(tmp_path: Path) -> None:
    coordinator, pinned, _thumbnails, _gallery, _toast, sidebar = _make(tmp_path / "Library")
    event = SimpleNamespace(
        library_root=tmp_path / "Library",
        changed_person_ids=["p1"],
        changed_group_ids=["g1"],
        person_redirects={"old": "p1"},
        group_redirects={},
    )

    coordinator.on_people_snapshot(event)

    assert pinned.pruned[0][0] == tmp_path / "Library"
    assert pinned.pruned[0][1]["person_ids"] == ("p1",)
    assert pinned.pruned[0][1]["group_ids"] == ("g1",)
    assert sidebar.refreshed == 1


def test_people_snapshot_for_another_library_only_refreshes_the_tree(tmp_path: Path) -> None:
    coordinator, pinned, _thumbnails, _gallery, _toast, sidebar = _make(tmp_path / "Library")
    event = SimpleNamespace(library_root=tmp_path / "Other")

    coordinator.on_people_snapshot(event)

    assert pinned.pruned == []
    assert sidebar.refreshed == 1


def test_successful_move_shows_the_toast(tmp_path: Path) -> None:
    coordinator, _pinned, _thumbnails, _gallery, toast, _sidebar = _make()

    coordinator.on_move_finished(tmp_path / "A", tmp_path / "B", True, "Moved 1 item.")

    assert toast.shown == ["Moved"]


def test_delete_and_failed_moves_do_not_show_the_toast(tmp_path: Path) -> None:
    trash = tmp_path / "Trash"
    coordinator, _pinned, _thumbnails, _gallery, toast, _sidebar = _make(
        trash_root=trash
    )

    coordinator.on_move_finished(tmp_path / "A", trash, True, "Moved to trash")
    coordinator.on_move_finished(trash, tmp_path / "A", True, "Restored")
    coordinator.on_move_finished(tmp_path / "A", tmp_path / "B", False, "Failed")

    assert toast.shown == []
