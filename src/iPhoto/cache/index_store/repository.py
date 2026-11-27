"""High-level repository interface for asset persistence.

This module provides the main API for CRUD operations on assets, delegating
infrastructure concerns to specialized components.

Architecture:
    This module implements a **Single Global Database** pattern where all metadata
    and asset information is stored in one centralized SQLite database at the
    library root. Key architectural principles:
    
    - **Single Write Gateway**: All write operations go through this repository
    - **Idempotent Writes**: Duplicate scans produce identical results (upsert)
    - **Additive-Only Scans**: Scanning never deletes data from the database
    - **Multiple Entry Points**: Scans can be triggered from any location
"""
from __future__ import annotations

import time

import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ...utils.logging import get_logger
from ...utils.pathutils import ensure_work_dir
from .engine import DatabaseManager
from .migrations import SchemaMigrator
from .recovery import RecoveryService
from .asset_queries import AssetQueryMixin
from .asset_writes import AssetWriteMixin
from ...config import SQLITE_BUSY_TIMEOUT_S

logger = get_logger()

from .sql_helpers import (  # noqa: F401 - re-exported for existing callers
    PHOTO_ROW_FILTER,
    _SQLITE_PARAM_CHUNK_SIZE,
    _sanitize_metadata_for_json,
)

# Database filename for the global index
GLOBAL_INDEX_DB_NAME = "global_index.db"


def index_db_path(library_root: Path) -> Path:
    """Return where the global index for *library_root* lives (no I/O)."""

    return ensure_work_dir(library_root) / GLOBAL_INDEX_DB_NAME



# Global singleton instance and lock for thread-safe access
_global_instance: Optional["AssetRepository"] = None
_global_lock = threading.Lock()




def get_global_repository(library_root: Path) -> "AssetRepository":
    """Get or create the global AssetRepository singleton for a library.
    
    This function ensures there is only one database instance for the entire
    application lifecycle, regardless of which entry point triggers a scan.
    
    Args:
        library_root: The root directory of the library.
        
    Returns:
        The singleton AssetRepository instance.
    """
    global _global_instance
    
    with _global_lock:
        resolved_root = library_root.resolve()
        
        if _global_instance is not None:
            # Verify the instance matches the requested root
            if _global_instance.library_root.resolve() == resolved_root:
                return _global_instance
            # Different root requested - close old instance and create new one
            logger.debug(
                "Switching global database from %s to %s",
                _global_instance.library_root,
                resolved_root,
            )
            _global_instance.close()
        
        _global_instance = AssetRepository(resolved_root)
        return _global_instance


def peek_global_repository(library_root: Path) -> "AssetRepository | None":
    """Return the open global repository when it already serves *library_root*.

    Read-only callers (coverage counters) use this to reuse the session's
    connection instead of constructing a second repository — construction runs
    the schema migrator, which costs more than the counts themselves. Unlike
    :func:`get_global_repository` this never switches or creates anything: a
    different root gets ``None``.
    """

    with _global_lock:
        if _global_instance is None:
            return None
        if _global_instance.library_root.resolve() != library_root.resolve():
            return None
        return _global_instance


def reset_global_repository() -> None:
    """Reset the global repository singleton.
    
    This is primarily used for testing to ensure clean state between tests.
    """
    global _global_instance
    
    with _global_lock:
        if _global_instance is not None:
            _global_instance.close()
            _global_instance = None


@contextmanager
def read_index(library_root: Path) -> Iterator["AssetRepository | None"]:
    """Yield an asset index for read-only aggregates.

    Borrows the session's global repository when it is already bound to
    *library_root* (one query, no construction), otherwise opens a short-lived
    repository and closes it again. Yields ``None`` when the library has never
    been scanned, so callers never create an index just to display "0 / 0".
    """

    store = peek_global_repository(library_root)
    if store is not None:
        yield store
        return
    path = index_db_path(library_root)
    if not path.exists():
        yield None
        return
    store = AssetRepository(library_root)
    try:
        yield store
    finally:
        store.close()


class AssetRepository(AssetWriteMixin, AssetQueryMixin):
    """High-level API for asset CRUD operations.
    
    This class implements the Single Write Gateway for the global database.
    All asset metadata operations (create, read, update, delete) must go
    through this repository to ensure data consistency.
    
    The repository uses idempotent write operations:
    - `append_rows`: Uses INSERT OR REPLACE (upsert) to avoid duplicates
    - `upsert_row`: Single-row upsert operation
    - Unique constraint on `rel` (file path) prevents duplicate entries
    
    Note: For the global database singleton, use `get_global_repository()`.
    """

    def __init__(self, library_root: Path):
        """Initialize the asset repository.
        
        Args:
            library_root: The root directory of the library. The global database
                will be created at `<library_root>/.iPhoto/global_index.db`.
        """
        self.library_root = library_root
        self.path = index_db_path(library_root)
        
        self._db_manager = DatabaseManager(self.path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        try:
            # ``with sqlite3.connect(...)`` commits but never closes the handle,
            # so every initialisation leaked a connection and kept the database
            # file locked (Windows then refused to replace or delete it with
            # WinError 32).
            with closing(sqlite3.connect(self.path, timeout=SQLITE_BUSY_TIMEOUT_S)) as conn:
                with conn:
                    SchemaMigrator.initialize_schema(conn)
        except sqlite3.DatabaseError as exc:
            logger.warning("Detected index.db corruption at %s: %s", self.path, exc)
            recovery = RecoveryService(
                self.path,
                SchemaMigrator.initialize_schema,
                self._db_row_to_dict,
                self._insert_rows,
            )
            recovery.recover()

    def transaction(self, *, begin_mode: str | None = None):
        """Context manager for batching multiple operations.
        
        Example:
            >>> with repo.transaction():
            ...     repo.upsert_row("a.jpg", {...})
            ...     repo.upsert_row("b.jpg", {...})
        """
        return self._db_manager.transaction(begin_mode=begin_mode)

    def close(self) -> None:
        """Close any active database connections.
        
        This method should be called when the repository is no longer needed,
        particularly when resetting the global singleton.
        """
        self._db_manager.close()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:
                # Best-effort close: do not propagate, but log for observability.
                logger.warning("Error while closing database connection: %s", exc)
            finally:
                self._conn = None



































    # Helper methods delegating to standalone functions in row_mapper


