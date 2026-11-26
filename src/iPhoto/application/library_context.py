"""The library a runtime or session is currently bound to.

Opening a library used to be implicit: any layer could call the process-wide
``get_global_repository(root)`` and receive "the" database for whatever root it
happened to name. Bundling the root, the repository and the on-disk workspace
into one value turns that implicit lookup into an explicit constructor input, so
"which library am I writing into" is answerable from a signature instead of
from the state of a module global.

Layer note: this module belongs to the application layer, so it only sees the
:class:`AssetRepositoryPort` protocol and a workspace directory. *How* either is
created is the composition root's decision; see
``iPhoto.infrastructure.repositories.library_context``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .ports.repositories import AssetRepositoryPort


@dataclass(frozen=True)
class LibraryContext:
    """An open library: its root, its asset repository and its work directory."""

    root: Path
    repository: AssetRepositoryPort
    workspace: Path

    @property
    def thumbnail_cache_dir(self) -> Path:
        """Return the thumbnail cache directory inside this library's workspace."""

        return self.workspace / "cache" / "thumbs"


class LibraryContextFactory(Protocol):
    """Open a :class:`LibraryContext` for a library root."""

    def open(self, library_root: Path) -> LibraryContext:
        """Return the context bound to *library_root*."""


__all__ = ["LibraryContext", "LibraryContextFactory"]
