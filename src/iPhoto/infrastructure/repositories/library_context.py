"""Open :class:`LibraryContext` values for libraries stored on disk.

This adapter is the only non-composition-root place that knows both the shared
index database (:func:`get_global_repository`) and the workspace layout
(``ensure_work_dir``). Everything above it receives the resulting context.
"""

from __future__ import annotations

from pathlib import Path

from ...application.library_context import LibraryContext
from ...cache.index_store import get_global_repository
from ...utils.pathutils import ensure_work_dir


class IndexStoreLibraryContextFactory:
    """Bind a library root to the shared index database and its work directory."""

    def open(self, library_root: Path) -> LibraryContext:
        root = Path(library_root)
        return LibraryContext(
            root=root,
            repository=get_global_repository(root),
            workspace=ensure_work_dir(root),
        )


__all__ = ["IndexStoreLibraryContextFactory"]
