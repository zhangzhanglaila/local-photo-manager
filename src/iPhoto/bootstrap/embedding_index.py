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


def load_stored_embeddings(
    library_root: Path,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return stored embedding rows for *library_root*.

    When *model_name* is given only that model's vectors are returned: vectors
    from another model live in a different space and must never be compared.
    """

    return list(
        get_embedding_repository(library_root).get_all_embeddings(model_name=model_name)
    )
