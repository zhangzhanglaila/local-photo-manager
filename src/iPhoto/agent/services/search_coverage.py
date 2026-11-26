"""How much of the library actually has vectors for the active model.

The settings page and the status entry need this before any indexing runs —
otherwise a restart shows "not indexed yet" even though tens of thousands of
vectors are sitting in the database.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...cache.index_store import read_index

_LOGGER = logging.getLogger(__name__)


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

    with read_index(library_root) as store:
        if store is None:
            return 0
        try:
            return int(store.photo_count())
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("could not count assets", exc_info=True)
            return 0


def compute_coverage(library_root: Path, model_name: str) -> tuple[int, int]:
    """Return ``(indexed, photos)`` for the active model."""

    return count_indexed(library_root, model_name), count_photos(library_root)


__all__ = ["compute_coverage", "count_indexed", "count_photos"]
