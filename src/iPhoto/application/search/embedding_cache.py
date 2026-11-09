"""Caching for stored embedding vectors.

Reading ~10k rows out of SQLite costs a few hundred milliseconds, and every search used
to do it again. The cache is keyed by library root and invalidated by the embeddings
database mtime, so a rebuilt index is picked up automatically.

The loader is injected rather than imported so the cache can be exercised without
touching a real library.
"""

from __future__ import annotations

from typing import Callable

Loader = Callable[[], list]


class EmbeddingCache:
    """Keep loaded embedding lists until their source changes."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, list]] = {}

    def get(self, key: str, mtime: float, loader: Loader) -> list:
        """Return embeddings for *key*, reloading when *mtime* changed.

        A zero ``mtime`` means "unknown": the value is loaded but not cached, because
        there is no way to tell later whether it went stale.
        """

        cached = self._entries.get(key)
        if cached is not None and mtime and cached[0] == mtime:
            return cached[1]

        embeddings = loader()
        if mtime:
            self._entries[key] = (mtime, embeddings)
        else:
            self._entries.pop(key, None)
        return embeddings

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
