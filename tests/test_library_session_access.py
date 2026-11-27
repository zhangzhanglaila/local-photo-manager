"""Session accessor: prefer the open LibrarySession, fall back to the manager."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.gui.coordinators import library_session_access


def _context(session=None, manager=None):
    return SimpleNamespace(library_session=session, library=manager or SimpleNamespace())


def test_services_come_from_the_active_session() -> None:
    root = Path("/library")
    session = SimpleNamespace(
        library_root=root,
        asset_queries="queries",
        asset_state="state",
        edit="edit",
        people="people",
        maps="maps",
        map_interactions="map-interactions",
    )
    access = library_session_access.LibrarySessionAccess(_context(session))

    assert access.active_session() is session
    assert access.library_root() == root
    assert access.asset_query_service() == "queries"
    assert access.asset_state_service() == "state"
    assert access.edit_service() == "edit"
    assert access.people_service(library_root=root) == "people"
    assert access.map_runtime() == "maps"
    assert access.map_interaction_service() == "map-interactions"


def test_services_fall_back_to_the_legacy_manager() -> None:
    root = Path("/library")
    manager = SimpleNamespace(
        root=lambda: root,
        asset_query_service="queries",
        asset_state_service="state",
        edit_service="edit",
        map_runtime="maps",
        map_interaction_service="map-interactions",
    )
    access = library_session_access.LibrarySessionAccess(_context(None, manager))

    assert access.active_session() is None
    assert access.library_root() == root
    assert access.asset_query_service() == "queries"
    assert access.asset_state_service() == "state"
    assert access.edit_service() == "edit"
    assert access.map_runtime() == "maps"
    assert access.map_interaction_service() == "map-interactions"


def test_people_service_for_another_root_uses_the_resolver(monkeypatch) -> None:
    session = SimpleNamespace(library_root=Path("/library"), people="session-people")
    manager = SimpleNamespace()
    sentinel = object()
    calls: list[tuple] = []

    def resolve(library, *, library_root):
        calls.append((library, library_root))
        return sentinel

    monkeypatch.setattr(library_session_access, "resolve_people_service", resolve)
    access = library_session_access.LibrarySessionAccess(_context(session, manager))

    assert access.people_service(library_root=Path("/other")) is sentinel
    assert calls == [(manager, Path("/other"))]
