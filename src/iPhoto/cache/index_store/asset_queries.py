"""Read side of the global asset index.

The read half of ``AssetRepository``: row lookups, pages, counts and the derived
lists the gallery, map and coverage screens ask for. Bodies moved verbatim; the
connection and schema live in the repository class.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from ...people.status import normalize_face_status
from ...utils.logging import get_logger
from .queries import QueryBuilder
from .row_mapper import db_row_to_dict
from .sql_helpers import PHOTO_ROW_FILTER

logger = get_logger()


class AssetQueryMixin:
    """Queries over asset rows: lookups, pages and counts."""

    def get_rows_by_rels(self, rels: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of ``rel`` → row dict for the given *rels*.

        Missing keys are silently omitted.  This is used by :class:`MoveWorker`
        to cache source rows before deletion so that the destination index can
        reuse existing metadata instead of re-extracting it with ExifTool.
        """
        rels_list = list(rels)
        if not rels_list:
            return {}

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(rels_list))
            query = f"SELECT * FROM assets WHERE rel IN ({placeholders})"
            cursor = conn.cursor()
            cursor.execute(query, rels_list)
            result: Dict[str, Dict[str, Any]] = {}
            for row in cursor:
                d = self._db_row_to_dict(row)
                rel_value = d.get("rel")
                if rel_value is not None:
                    result[str(rel_value)] = d
            return result
        finally:
            if should_close:
                conn.close()
    def get_rows_by_ids(self, asset_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of ``asset.id`` to asset rows."""

        ids_list = [str(asset_id) for asset_id in asset_ids if asset_id]
        if not ids_list:
            return {}

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(ids_list))
            query = f"SELECT * FROM assets WHERE id IN ({placeholders})"
            cursor = conn.cursor()
            cursor.execute(query, ids_list)
            rows: Dict[str, Dict[str, Any]] = {}
            for row in cursor:
                data = self._db_row_to_dict(row)
                asset_id = data.get("id")
                if isinstance(asset_id, str) and asset_id and asset_id not in rows:
                    rows[asset_id] = data
            return rows
        finally:
            if should_close:
                conn.close()
    def read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield rows whose ``face_status`` matches one of *statuses*."""

        normalized_statuses = [
            status
            for status in (normalize_face_status(value) for value in statuses)
            if status is not None
        ]
        if not normalized_statuses:
            return

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(normalized_statuses))
            query = f"SELECT * FROM assets WHERE face_status IN ({placeholders}) ORDER BY dt DESC, id DESC"
            params: list[Any] = list(normalized_statuses)
            if limit is not None:
                query += " LIMIT ?"
                params.append(int(limit))
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()
    def read_all(
        self,
        sort_by_date: bool = False,
        filter_hidden: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        """Yield all rows from the index.
        
        Args:
            sort_by_date: If True, order results by 'dt' descending (newest first).
            filter_hidden: If True, exclude hidden assets (e.g. motion components).
        """
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            query = "SELECT * FROM assets"
            where_clauses = []

            if filter_hidden:
                where_clauses.append("live_role = 0")

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            if sort_by_date:
                query += " ORDER BY dt DESC NULLS LAST, id DESC"

            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()
    def count_by_face_status(self) -> Dict[str, int]:
        """Return a status-to-count mapping for ``assets.face_status``."""

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            cursor = conn.execute(
                "SELECT face_status, COUNT(*) AS asset_count FROM assets GROUP BY face_status"
            )
            counts: Dict[str, int] = {}
            for status, asset_count in cursor.fetchall():
                normalized = normalize_face_status(status)
                if normalized is None:
                    continue
                counts[normalized] = int(asset_count or 0)
            return counts
        finally:
            if should_close:
                conn.close()
    def count_photos_by_face_status(self, statuses: Iterable[str]) -> int:
        """Number of still photos whose ``face_status`` is one of *statuses*."""

        normalized = sorted(
            value
            for value in (normalize_face_status(status) for status in statuses)
            if value
        )
        if not normalized:
            return 0
        placeholders = ", ".join("?" for _ in normalized)
        return self._scalar_count(
            f"SELECT COUNT(*) FROM assets WHERE {PHOTO_ROW_FILTER}"
            f" AND face_status IN ({placeholders})",
            tuple(normalized),
        )
    def count_by_year_month(
        self,
        *,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[int, int, int]]:
        """Return ``(year, month, count)`` buckets for the current scope.

        The timeline scrubber needs to know where each year starts without
        loading every asset. This is a single aggregate query — one row per
        month, a few hundred at most — so it stays cheap even on large
        libraries, unlike walking the rows to count them.
        """

        query, params = QueryBuilder.build_pagination_query(
            select_clause="SELECT year, month, COUNT(*) AS bucket_count",
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            sort_by_date=False,
            limit=None,
        )
        query += " GROUP BY year, month ORDER BY year DESC, month DESC"

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            buckets: List[Tuple[int, int, int]] = []
            for year, month, count in cursor:
                try:
                    buckets.append((int(year or 0), int(month or 0), int(count or 0)))
                except (TypeError, ValueError):
                    continue
            return buckets
        finally:
            if should_close:
                conn.close()
    def read_geotagged(self) -> Iterator[Dict[str, Any]]:
        """Yield only rows that contain GPS metadata."""
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            query = "SELECT * FROM assets WHERE gps IS NOT NULL"
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            for row in cursor:
                d = self._db_row_to_dict(row)
                if isinstance(d.get("gps"), dict):
                    yield d
        finally:
            if should_close:
                conn.close()
    def get_assets_page(
        self,
        cursor_dt: Optional[str] = None,
        cursor_id: Optional[str] = None,
        limit: int = 100,
        album_path: Optional[str] = None,
        include_subalbums: bool = False,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch a page of assets using cursor-based pagination.
        
        This method uses Seek Pagination (keyset pagination) for efficient
        retrieval of large datasets.
        
        Args:
            cursor_dt: The timestamp of the last item from the previous page.
            cursor_id: The ID of the last item from the previous page.
            limit: Maximum number of items to return (default: 100).
            album_path: If provided, filter to assets in this album path.
            include_subalbums: If True, include assets from sub-albums.
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.
            offset: Number of sorted rows to skip.
        
        Returns:
            A list of asset dictionaries for the requested page.
        """
        query, params = QueryBuilder.build_pagination_query(
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            cursor_dt=cursor_dt,
            cursor_id=cursor_id,
            limit=limit,
            offset=offset,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            results = []
            for row in cursor:
                results.append(self._db_row_to_dict(row))
            return results
        finally:
            if should_close:
                conn.close()
    def read_geometry_only(
        self,
        filter_params: Optional[Dict[str, Any]] = None,
        sort_by_date: bool = True,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Yield lightweight asset rows for fast grid layout.
        
        Fetches only the columns strictly required for grid layout, badges,
        and sorting.
        
        Args:
            filter_params: Optional dictionary of SQL filter criteria.
            sort_by_date: If True, sort results by date descending.
            album_path: If provided, filter to assets in this album path.
            include_subalbums: If True, include assets from sub-albums.
        """
        # Columns needed for the lightweight "viewport-first" loading strategy
        columns = [
            "id", "rel", "aspect_ratio", "media_type", "live_partner_rel",
            "dur", "year", "month", "dt", "ts", "content_id", "bytes",
            "mime", "w", "h", "original_rel_path", "original_album_id",
            "original_album_subpath", "is_favorite", "location", "gps",
            "face_status",
            "micro_thumbnail"
        ]

        logger.debug(
            "IndexStore.read_geometry_only album_path=%s include_subalbums=%s "
            "sort_by_date=%s filter_params=%s",
            album_path,
            include_subalbums,
            sort_by_date,
            filter_params,
        )

        query, params = QueryBuilder.build_pagination_query(
            select_clause=f"SELECT {', '.join(columns)}",
            base_where=["live_role = 0"],
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_params=filter_params,
            sort_by_date=sort_by_date,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor:
                d = dict(row)
                # Parse GPS if present (stored as JSON string)
                if d.get("gps"):
                    try:
                        d["gps"] = json.loads(d["gps"])
                    except (json.JSONDecodeError, TypeError):
                        d["gps"] = None
                yield d
        finally:
            if should_close:
                conn.close()
    def read_album_assets(
        self,
        album_path: str,
        include_subalbums: bool = False,
        sort_by_date: bool = True,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield assets belonging to a specific album.
        
        Args:
            album_path: The album path to filter (e.g., "2023/Trip").
            include_subalbums: If True, include assets from sub-albums.
            sort_by_date: If True, order results by date descending.
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.
        
        Yields:
            Asset dictionaries for the matching album(s).
        """
        query, params = QueryBuilder.build_pagination_query(
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            sort_by_date=sort_by_date,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()
    def count(
        self,
        filter_hidden: bool = False,
        filter_params: Optional[Dict[str, Any]] = None,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
    ) -> int:
        """Return the total number of assets matching the given filters.
        
        Args:
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.
            album_path: If provided, filter to assets in this album path.
            include_subalbums: If True, include assets from sub-albums.
        
        Returns:
            The number of assets matching the filters.
        """
        query, params = QueryBuilder.build_pagination_query(
            select_clause="SELECT COUNT(*)",
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            sort_by_date=False,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            if should_close:
                conn.close()
    def has_any_assets(
        self,
        filter_hidden: bool = False,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
    ) -> bool:
        """Return True if at least one asset matches the given filters.

        Faster than ``count()`` for existence checks because it uses LIMIT 1.
        """
        query, params = QueryBuilder.build_pagination_query(
            select_clause="SELECT 1",
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            sort_by_date=False,
            limit=1,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            cursor = conn.execute(query, params)
            return cursor.fetchone() is not None
        finally:
            if should_close:
                conn.close()
    def photo_count(self) -> int:
        """Number of still photos: videos and live-photo motion parts excluded."""

        return self._scalar_count(f"SELECT COUNT(*) FROM assets WHERE {PHOTO_ROW_FILTER}")
    def _scalar_count(self, query: str, params: Tuple[Any, ...] = ()) -> int:
        """Run a single-row ``COUNT(*)`` query on the shared connection."""

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)
        try:
            row = conn.execute(query, params).fetchone()
            return int(row[0]) if row else 0
        finally:
            if should_close:
                conn.close()
    def metadata_version(self) -> int:
        """Return the extraction version stamped into this index (0 when unset)."""

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            return int(row[0]) if row else 0
        finally:
            if should_close:
                conn.close()
    def list_albums(self) -> List[str]:
        """Return a list of distinct album paths in the index."""
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            cursor = conn.execute(
                "SELECT DISTINCT parent_album_path FROM assets "
                "WHERE parent_album_path IS NOT NULL "
                "ORDER BY parent_album_path"
            )
            return [row[0] for row in cursor if row[0]]
        finally:
            if should_close:
                conn.close()
    def count_album_assets(
        self,
        album_path: str,
        include_subalbums: bool = False,
        filter_hidden: bool = True,
    ) -> int:
        """Return the count of assets in a specific album.
        
        Args:
            album_path: The album path to count.
            include_subalbums: If True, include assets from sub-albums.
            filter_hidden: If True, exclude hidden assets.
        
        Returns:
            The number of assets matching the criteria.
        """
        return self.count(
            filter_hidden=filter_hidden,
            album_path=album_path,
            include_subalbums=include_subalbums,
        )
    def _db_row_to_dict(self, db_row: sqlite3.Row) -> Dict[str, Any]:
        """Map a DB row back to a dictionary."""
        return db_row_to_dict(db_row)


__all__ = ["AssetQueryMixin"]
