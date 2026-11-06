"""Repository for storing and retrieving image embeddings."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

from ...utils.logging import get_logger
from ...utils.pathutils import ensure_work_dir
from ...config import SQLITE_BUSY_TIMEOUT_S

logger = get_logger()

EMBEDDING_DB_NAME = "embeddings.db"

_global_instance: Optional["EmbeddingRepository"] = None
_global_lock = threading.Lock()


def get_embedding_repository(library_root: Path) -> "EmbeddingRepository":
    """Get or create the global EmbeddingRepository singleton."""
    global _global_instance

    with _global_lock:
        resolved_root = library_root.resolve()

        if _global_instance is not None:
            if _global_instance.library_root.resolve() == resolved_root:
                return _global_instance
            _global_instance.close()

        _global_instance = EmbeddingRepository(resolved_root)
        return _global_instance


class EmbeddingRepository:
    """Repository for storing and retrieving image embeddings."""

    def __init__(self, library_root: Path):
        """Initialize the embedding repository."""
        self.library_root = library_root
        self.path = ensure_work_dir(library_root) / EMBEDDING_DB_NAME
        self._local = threading.local()  # Thread-local connections
        self._cache: Optional[List[dict]] = None  # In-memory cache
        self._cache_dirty = True  # Whether cache needs refresh
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.path), timeout=SQLITE_BUSY_TIMEOUT_S)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                asset_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                dimension INTEGER NOT NULL,
                model_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_asset_id
            ON embeddings(asset_id)
        """)
        conn.commit()

    def store_embedding(
        self,
        asset_id: str,
        embedding: np.ndarray,
        model_name: Optional[str] = None,
    ) -> None:
        """Store an embedding for an asset."""
        conn = self._get_conn()
        embedding_bytes = embedding.astype(np.float32).tobytes()

        conn.execute(
            """
            INSERT OR REPLACE INTO embeddings (asset_id, embedding, dimension, model_name)
            VALUES (?, ?, ?, ?)
            """,
            (asset_id, embedding_bytes, len(embedding), model_name),
        )
        conn.commit()
        self._cache_dirty = True  # Invalidate cache

    def store_embeddings_batch(
        self,
        embeddings: List[tuple],
    ) -> None:
        """Store multiple embeddings in a batch."""
        conn = self._get_conn()

        data = []
        for asset_id, embedding, model_name in embeddings:
            embedding_bytes = embedding.astype(np.float32).tobytes()
            data.append((asset_id, embedding_bytes, len(embedding), model_name))

        conn.executemany(
            """
            INSERT OR REPLACE INTO embeddings (asset_id, embedding, dimension, model_name)
            VALUES (?, ?, ?, ?)
            """,
            data,
        )
        conn.commit()
        self._cache_dirty = True  # Invalidate cache

    def get_all_embeddings(self) -> List[dict]:
        """Get all stored embeddings (with in-memory cache)."""
        # Return cached results if available
        if not self._cache_dirty and self._cache is not None:
            return self._cache

        conn = self._get_conn()
        cursor = conn.execute("SELECT asset_id, embedding, dimension FROM embeddings")

        results = []
        for row in cursor:
            asset_id, embedding_bytes, dimension = row
            embedding = np.frombuffer(embedding_bytes, dtype=np.float32).copy()
            results.append({
                "asset_id": asset_id,
                "embedding": embedding,
            })

        # Update cache
        self._cache = results
        self._cache_dirty = False

        return results

    def get_asset_ids_without_embeddings(self, asset_ids: List[str]) -> List[str]:
        """Get asset IDs that don't have embeddings yet."""
        if not asset_ids:
            return []

        conn = self._get_conn()
        existing = set()

        chunk_size = 900
        for i in range(0, len(asset_ids), chunk_size):
            chunk = asset_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor = conn.execute(
                f"SELECT asset_id FROM embeddings WHERE asset_id IN ({placeholders})",
                chunk,
            )
            existing.update(row[0] for row in cursor)

        return [aid for aid in asset_ids if aid not in existing]

    def count(self) -> int:
        """Return the number of stored embeddings."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM embeddings")
        return cursor.fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
