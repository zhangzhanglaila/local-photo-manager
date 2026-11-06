"""The services that an open library session binds, as one all-or-nothing value.

``LibraryRuntimeController`` used to keep twelve independent ``Optional`` fields.
Binding was a twelve-step ritual, and a half-finished bind left the controller in a
state that "looks bound" (some services present) while others were still ``None`` —
the kind of partial state that callers then had to probe for.

Keeping the services in one immutable value makes the lifecycle explicit: a session
is either bound (all fields set) or not (the registry is ``None``/empty). Each
``bind_*`` setter still exists for compatibility, but it now replaces one field of
an immutable snapshot instead of mutating a loose collection of attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .library_session import LibrarySession

#: Field names, in binding order. Used by ``from_session`` and the tests.
SERVICE_FIELDS: tuple[str, ...] = (
    "scan_service",
    "asset_query_service",
    "state_repository",
    "asset_state_service",
    "album_metadata_service",
    "asset_lifecycle_service",
    "asset_operation_service",
    "people_service",
    "map_runtime",
    "map_interaction_service",
    "edit_service",
    "location_service",
)


@dataclass(frozen=True)
class LibraryServiceRegistry:
    """Immutable snapshot of the services bound to one library session."""

    scan_service: Any = None
    asset_query_service: Any = None
    state_repository: Any = None
    asset_state_service: Any = None
    album_metadata_service: Any = None
    asset_lifecycle_service: Any = None
    asset_operation_service: Any = None
    people_service: Any = None
    map_runtime: Any = None
    map_interaction_service: Any = None
    edit_service: Any = None
    location_service: Any = None

    @classmethod
    def from_session(cls, session: "LibrarySession") -> "LibraryServiceRegistry":
        """Read every service from *session* in one step."""

        return cls(
            scan_service=session.scans,
            asset_query_service=session.asset_queries,
            state_repository=session.state,
            asset_state_service=session.asset_state,
            album_metadata_service=session.album_metadata,
            asset_lifecycle_service=session.asset_lifecycle,
            asset_operation_service=session.asset_operations,
            people_service=session.people,
            map_runtime=session.maps,
            map_interaction_service=session.map_interactions,
            edit_service=session.edit,
            location_service=session.locations,
        )

    def with_service(self, field: str, value: Any) -> "LibraryServiceRegistry":
        """Return a copy with *field* replaced."""

        if field not in SERVICE_FIELDS:
            raise KeyError(f"Unknown library service field: {field}")
        return replace(self, **{field: value})

    def missing(self) -> tuple[str, ...]:
        """Return the fields that are not bound yet (empty when fully bound)."""

        return tuple(name for name in SERVICE_FIELDS if getattr(self, name) is None)


__all__ = ["LibraryServiceRegistry", "SERVICE_FIELDS"]
