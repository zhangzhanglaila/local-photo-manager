"""Read stored CLIP embeddings without leaking the concrete index store.

The GUI used to import ``iPhoto.cache.index_store.embedding_repository`` directly,
which the layer guard forbids: presentation code must not reach into a concrete
storage implementation. This module keeps that single dependency in the bootstrap
layer, which is allowed to wire infrastructure together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cache.index_store.embedding_repository import get_embedding_repository


def load_stored_embeddings(library_root: Path) -> list[dict[str, Any]]:
    """Return every stored embedding row for *library_root*."""

    return list(get_embedding_repository(library_root).get_all_embeddings())
