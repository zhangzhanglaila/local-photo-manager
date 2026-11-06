"""Library services are bound as one snapshot, not twelve loose attributes.

The controller used to keep twelve independent ``Optional`` fields, so a partially
finished bind looked like a bound library. The registry makes the state explicit:
one immutable snapshot, and ``missing()`` says what is not bound yet.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.library.service_registry import SERVICE_FIELDS, LibraryServiceRegistry


def _session() -> SimpleNamespace:
    """A stand-in with the twelve attributes ``from_session`` reads."""

    return SimpleNamespace(
        scans=object(),
        asset_queries=object(),
        state=object(),
        asset_state=object(),
        album_metadata=object(),
        asset_lifecycle=object(),
        asset_operations=object(),
        people=object(),
        maps=object(),
        map_interactions=object(),
        edit=object(),
        locations=object(),
    )


def test_from_session_reads_every_field() -> None:
    session = _session()

    registry = LibraryServiceRegistry.from_session(session)

    assert registry.missing() == ()
    assert registry.scan_service is session.scans
    assert registry.location_service is session.locations


def test_empty_registry_reports_what_is_missing() -> None:
    registry = LibraryServiceRegistry()

    assert set(registry.missing()) == set(SERVICE_FIELDS)


def test_with_service_returns_a_new_snapshot() -> None:
    original = LibraryServiceRegistry()
    scan_service = object()

    updated = original.with_service("scan_service", scan_service)

    assert updated.scan_service is scan_service
    assert original.scan_service is None, "the previous snapshot must not mutate"
    assert "scan_service" not in updated.missing()


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(KeyError):
        LibraryServiceRegistry().with_service("not_a_service", object())


def test_controller_starts_unbound_and_exposes_no_services() -> None:
    controller = LibraryRuntimeController()

    assert controller.library_session is None
    for field in SERVICE_FIELDS:
        assert getattr(controller, field) is None, f"{field} must start unbound"
