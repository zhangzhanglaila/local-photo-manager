"""Library state repository adapter backed by the current index store."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...application.library_context import LibraryContextFactory
from ...application.ports import LibraryStateRepositoryPort
from ...application.ports.repositories import AssetRepositoryPort


class IndexStoreLibraryStateRepository(LibraryStateRepositoryPort):
    """Persist durable user choices through the current global index store.

    The adapter resolves its repository through an injected
    :class:`LibraryContextFactory` on every call instead of reaching for the
    process-wide singleton itself: the context stays fresh if the library is
    closed and reopened, and tests can bind a fake repository without patching
    module globals.
    """

    def __init__(self, library_root: Path, *, contexts: LibraryContextFactory) -> None:
        self._library_root = Path(library_root)
        self._contexts = contexts

    @property
    def library_root(self) -> Path:
        """Return the library this adapter writes into."""

        return self._library_root

    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        self._repository().set_favorite_status(rel, is_favorite)

    def sync_favorites(self, featured_rels: Iterable[str]) -> None:
        self._repository().sync_favorites(featured_rels)

    def update_location(self, rel: str, location: str) -> None:
        self._repository().update_location(rel, location)

    def update_asset_geodata(
        self,
        rel: str,
        *,
        gps: dict[str, float] | None,
        location: str | None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        self._repository().update_asset_geodata(
            rel,
            gps=gps,
            location=location,
            metadata_updates=metadata_updates,
        )

    def _repository(self) -> AssetRepositoryPort:
        return self._contexts.open(self._library_root).repository


__all__ = ["IndexStoreLibraryStateRepository"]
