"""How much of the library actually has vectors for the active model.

The settings page and the status entry need this before any indexing runs —
otherwise a restart shows "not indexed yet" even though tens of thousands of
vectors are sitting in the database.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def _db_path(library_root: Path) -> Path | None:
    try:
        from ...cache.index_store import IndexStore

        path = Path(IndexStore(library_root).path)
    except Exception:  # pragma: no cover - defensive
        _LOGGER.debug("could not resolve the index database", exc_info=True)
        return None
    return path if path.exists() else None


def count_indexed(library_root: Path, model_name: str) -> int:
    """Number of vectors stored for *model_name*."""

    try:
        from ...cache.index_store.embedding_repository import (
            get_embedding_repository,
        )

        return int(get_embedding_repository(library_root).count(model_name=model_name))
    except Exception:  # pragma: no cover - defensive
        _LOGGER.debug("could not count embeddings", exc_info=True)
        return 0


def count_photos(library_root: Path) -> int:
    """Number of still photos in the library (the searchable population)."""

    path = _db_path(library_root)
    if path is None:
        return 0
    connection = sqlite3.connect(str(path))
    try:
        # The index store has no "deleted" column: deletion physically removes
        # rows, so media type plus the live-photo role is the whole filter.
        row = connection.execute(
            "SELECT COUNT(*) FROM assets WHERE media_type = 0"
            " AND (live_role IS NULL OR live_role = 0)"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.DatabaseError:
        _LOGGER.debug("could not count assets", exc_info=True)
        return 0
    finally:
        connection.close()


def compute_coverage(library_root: Path, model_name: str) -> tuple[int, int]:
    """Return ``(indexed, photos)`` for the active model."""

    return count_indexed(library_root, model_name), count_photos(library_root)


__all__ = ["compute_coverage", "count_indexed", "count_photos"]
