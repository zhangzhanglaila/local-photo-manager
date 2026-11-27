"""Read the active library session's services through one small accessor.

Every getter used to live on ``MainCoordinator`` and repeat the same pattern:
prefer the open ``LibrarySession``, otherwise fall back to the legacy library
manager. Keeping that fallback in one object makes the session boundary visible
and keeps the coordinator from owning it.
"""

from __future__ import annotations

from pathlib import Path

from iPhoto.gui.services.people_service_resolver import resolve_people_service


class LibrarySessionAccess:
    """Resolve library-scoped services from the active session or manager."""

    def __init__(self, context) -> None:
        self._context = context

    def active_session(self):
        return getattr(self._context, "library_session", None)

    def library_root(self) -> Path | None:
        session = self.active_session()
        if session is not None:
            return getattr(session, "library_root", None)
        return self._context.library.root()

    def asset_query_service(self):
        session = self.active_session()
        if session is not None:
            return getattr(session, "asset_queries", None)
        return getattr(self._context.library, "asset_query_service", None)

    def asset_state_service(self):
        session = self.active_session()
        if session is not None:
            return getattr(session, "asset_state", None)
        return getattr(self._context.library, "asset_state_service", None)

    def edit_service(self):
        session = self.active_session()
        if session is not None:
            return getattr(session, "edit", None)
        return getattr(self._context.library, "edit_service", None)

    def people_service(self, library_root: Path | None = None):
        session = self.active_session()
        session_root = (
            getattr(session, "library_root", None) if session is not None else None
        )
        if session is not None and (
            library_root is None or session_root == library_root
        ):
            return getattr(session, "people", None)
        return resolve_people_service(
            self._context.library,
            library_root=library_root,
        )

    def map_runtime(self):
        session = self.active_session()
        if session is not None:
            return getattr(session, "maps", None)
        return getattr(self._context.library, "map_runtime", None)

    def map_interaction_service(self):
        session = self.active_session()
        if session is not None:
            return getattr(session, "map_interactions", None)
        return getattr(self._context.library, "map_interaction_service", None)


__all__ = ["LibrarySessionAccess"]
