"""The organize commands only need session, gallery view model and status bar."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iPhoto.gui.coordinators import organize_coordinator
from iPhoto.gui.coordinators.organize_coordinator import OrganizeCoordinator
from iPhoto.i18n import tr


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_message(self, message: str, timeout: int = 0) -> None:
        del timeout
        self.messages.append(message)


class _Gallery:
    def __init__(self) -> None:
        self.shown: list[list[str]] = []

    def show_search_results(self, asset_ids) -> None:
        self.shown.append(list(asset_ids))


def _coordinator():
    context = SimpleNamespace(library_session=object())
    gallery = _Gallery()
    status = _StatusBar()
    return OrganizeCoordinator(context, gallery, status), gallery, status


def test_find_duplicates_without_a_session_does_nothing(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        organize_coordinator.organize_workers,
        "find_duplicates",
        lambda *args, **kwargs: called.append(args),
    )
    coordinator = OrganizeCoordinator(
        SimpleNamespace(library_session=None), _Gallery(), _StatusBar()
    )

    coordinator.find_duplicates()

    assert called == []


def test_find_duplicates_reports_progress_and_flattens_groups(monkeypatch) -> None:
    captured = {}

    def fake_find(session, callback):
        captured["session"] = session
        captured["callback"] = callback

    monkeypatch.setattr(
        organize_coordinator.organize_workers, "find_duplicates", fake_find
    )
    coordinator, gallery, status = _coordinator()

    coordinator.find_duplicates()
    assert status.messages == [tr("organize.searching_duplicates")]

    captured["callback"](
        [
            SimpleNamespace(asset_ids=["a", "b"]),
            SimpleNamespace(asset_ids=["c"]),
        ]
    )

    assert gallery.shown == [["a", "b", "c"]]
    assert status.messages[-1] == tr(
        "organize.duplicates_found", groups=2, photos=3
    )


def test_find_duplicates_explains_an_empty_result(monkeypatch) -> None:
    monkeypatch.setattr(
        organize_coordinator.organize_workers,
        "find_duplicates",
        lambda session, callback: callback([]),
    )
    coordinator, gallery, status = _coordinator()

    coordinator.find_duplicates()

    assert gallery.shown == []
    assert status.messages == [
        tr("organize.no_duplicates"),
        tr("organize.searching_duplicates"),
    ]


def test_smart_albums_show_the_first_album_and_explain_an_empty_result(
    monkeypatch,
) -> None:
    callbacks = []

    def fake_create(session, group_by, callback):
        callbacks.append((session, group_by, callback))

    monkeypatch.setattr(
        organize_coordinator.organize_workers, "create_smart_albums", fake_create
    )
    coordinator, gallery, status = _coordinator()

    coordinator.create_smart_albums("time")
    assert status.messages == [
        tr("organize.creating_albums", group_by=tr("organize.group.time"))
    ]

    _session, group_by, callback = callbacks[-1]
    assert group_by == "time"
    callback([SimpleNamespace(asset_ids=["x", "y"])])
    assert gallery.shown == [["x", "y"]]
    assert status.messages[-1] == tr("organize.albums_created", default="Created 1 smart albums")

    callback([])
    assert status.messages[-1] == tr("organize.no_albums")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
