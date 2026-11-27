"""People dashboard activations open the gallery cluster with the right query."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from iPhoto.gui.coordinators.people_navigation_coordinator import (
    PeopleNavigationCoordinator,
)


class _Page:
    def __init__(self, asset_ids: list[str], *, fail: bool = False) -> None:
        self._query = SimpleNamespace(asset_ids=asset_ids)
        self._fail = fail
        self.prepared = 0

    def build_cluster_query(self, person_id: str):
        if self._fail:
            raise RuntimeError("boom")
        self.person_id = person_id
        return self._query

    def build_group_query(self, group_id: str):
        self.group_id = group_id
        return self._query

    def prepare_for_hide(self) -> None:
        self.prepared += 1


class _Gallery:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def open_people_cluster_gallery(self, query, *, kind: str, entity_id: str) -> None:
        self.calls.append((query, kind, entity_id))


class _Router:
    def __init__(self) -> None:
        self.shown = 0

    def show_gallery(self) -> None:
        self.shown += 1


def _make(asset_ids: list[str], *, fail: bool = False):
    page = _Page(asset_ids, fail=fail)
    gallery = _Gallery()
    router = _Router()
    coordinator = PeopleNavigationCoordinator(
        SimpleNamespace(ui=SimpleNamespace(people_page=page)),
        gallery,
        router,
    )
    return coordinator, page, gallery, router


def _run_immediately(delay, callback):
    del delay
    callback()


def test_person_activation_opens_the_cluster_gallery() -> None:
    coordinator, page, gallery, router = _make(["a", "b"])

    with patch(
        "iPhoto.gui.coordinators.people_navigation_coordinator.QTimer"
    ) as timer:
        timer.singleShot.side_effect = _run_immediately
        coordinator.on_cluster_activated("person-1")

    assert page.person_id == "person-1"
    assert page.prepared == 1
    assert gallery.calls[0][1:] == ("person", "person-1")
    assert router.shown == 1


def test_group_activation_opens_the_group_gallery() -> None:
    coordinator, page, gallery, _router = _make(["a"])

    with patch(
        "iPhoto.gui.coordinators.people_navigation_coordinator.QTimer"
    ) as timer:
        timer.singleShot.side_effect = _run_immediately
        coordinator.on_group_activated("group-1")

    assert page.group_id == "group-1"
    assert gallery.calls[0][1:] == ("group", "group-1")


def test_empty_query_does_not_open_the_gallery() -> None:
    coordinator, page, gallery, router = _make([])

    with patch(
        "iPhoto.gui.coordinators.people_navigation_coordinator.QTimer"
    ) as timer:
        timer.singleShot.side_effect = _run_immediately
        coordinator.on_cluster_activated("person-1")

    assert page.prepared == 0
    assert gallery.calls == []
    assert router.shown == 0


def test_query_failure_is_logged_and_does_not_raise(caplog) -> None:
    coordinator, _page, gallery, router = _make([], fail=True)

    with patch(
        "iPhoto.gui.coordinators.people_navigation_coordinator.QTimer"
    ) as timer:
        timer.singleShot.side_effect = _run_immediately
        coordinator.on_cluster_activated("person-1")

    assert gallery.calls == []
    assert router.shown == 0
    assert "Failed to open people person gallery for person-1" in caplog.text
