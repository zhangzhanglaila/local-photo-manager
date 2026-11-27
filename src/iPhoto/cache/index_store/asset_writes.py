"""Write side of the global asset index.

`AssetRepository` had 41 methods in one 935-line class. This mixin holds every
write: scans, upserts, removals, favourites, face status, GPS and live-photo
roles. It takes the repository as ``self`` so the connection, transaction helper
and schema stay in one place. Bodies moved verbatim; no SQL changed.
"""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from typing import Iterable

from ...people.status import normalize_face_status
from .row_mapper import insert_rows, row_to_db_params
from .scan_merge import merge_scan_rows as merge_scan_rows_payload
from .sql_helpers import _SQLITE_PARAM_CHUNK_SIZE, _sanitize_metadata_for_json


class AssetWriteMixin:
    """All mutating operations on asset rows."""

    def write_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Rewrite the entire index with *rows*."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM assets")
            self._insert_rows(conn, rows)
    def append_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Merge *rows* into the index, replacing duplicates by ``rel`` key."""
        with self.transaction() as conn:
            self._insert_rows(conn, rows)
    def merge_scan_rows(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge scanned rows while preserving persisted library-managed state.

        The SELECT for existing rows and the subsequent INSERT are performed
        within the same transaction so the read/write is atomic and cannot
        lose concurrent updates to ``face_status`` or other library-managed
        fields.

        Existing rows are fetched in chunks of at most
        ``_SQLITE_PARAM_CHUNK_SIZE`` to avoid hitting SQLite's bound-parameter
        limit when a large snapshot is passed (e.g. from
        ``index_sync_service.update_index_snapshot``).
        """

        materialized_rows = [dict(row) for row in rows]
        if not materialized_rows:
            return []

        # De-duplicate while preserving insertion order so each rel is queried
        # at most once.
        unique_rels: List[str] = list(
            dict.fromkeys(str(row["rel"]) for row in materialized_rows if row.get("rel"))
        )

        with self.transaction(begin_mode="IMMEDIATE") as conn:
            existing_rows_by_rel: Dict[str, Dict[str, Any]] = {}
            if unique_rels:
                conn.row_factory = sqlite3.Row
                for offset in range(0, len(unique_rels), _SQLITE_PARAM_CHUNK_SIZE):
                    chunk = unique_rels[offset : offset + _SQLITE_PARAM_CHUNK_SIZE]
                    placeholders = ", ".join(["?"] * len(chunk))
                    cursor = conn.execute(
                        f"SELECT rel, id, bytes, ts, face_status, "
                        f"is_favorite, original_rel_path, original_album_id, "
                        f"original_album_subpath, live_role, live_partner_rel "
                        f"FROM assets WHERE rel IN ({placeholders})",
                        chunk,
                    )
                    for db_row in cursor:
                        d = self._db_row_to_dict(db_row)
                        rel_value = d.get("rel")
                        if rel_value is not None:
                            existing_rows_by_rel[str(rel_value)] = d
                conn.row_factory = None

            merged_rows = merge_scan_rows_payload(materialized_rows, existing_rows_by_rel)
            _write_started = time.monotonic()
            self._insert_rows(conn, merged_rows)
            from ...utils.logging import diag

            diag(
                "[DIAG] scan write: rows=%d txn=%.0f ms",
                len(merged_rows),
                (time.monotonic() - _write_started) * 1000,
            )

        return merged_rows
    def upsert_row(self, rel: str, row: Dict[str, Any]) -> None:
        """Insert or update a single row identified by *rel*."""
        row_data = row.copy()
        row_data["rel"] = rel
        with self.transaction() as conn:
            self._insert_rows(conn, [row_data])
    def remove_rows(self, rels: Iterable[str]) -> None:
        """Drop any index rows whose ``rel`` key matches *rels*."""
        removable = list(rels)
        if not removable:
            return

        placeholders = ", ".join(["?"] * len(removable))
        query = f"DELETE FROM assets WHERE rel IN ({placeholders})"
        self._db_manager.execute_in_transaction(query, removable)
    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        """Toggle the favorite status for a single asset efficiently."""
        val = 1 if is_favorite else 0
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET is_favorite = ? WHERE rel = ?",
            (val, rel),
        )
    def sync_favorites(self, featured_rels: Iterable[str]) -> None:
        """Synchronise the DB 'is_favorite' column with the provided list."""
        featured_rels_list = list(featured_rels)
        
        # Normalize input paths to ensure consistent comparison (NFC)
        input_normalized_map = {
            unicodedata.normalize("NFC", r): r for r in featured_rels_list
        }
        featured_normalized_set = set(input_normalized_map.keys())

        with self.transaction() as conn:
            # Fetch all rels from the DB to build a normalized-to-original mapping
            cursor = conn.execute("SELECT rel FROM assets")
            all_rels_map = {
                unicodedata.normalize("NFC", row[0]): row[0] for row in cursor
            }

            # Fetch currently marked favorites
            current_favs_normalized = {
                unicodedata.normalize("NFC", row[0])
                for row in conn.execute("SELECT rel FROM assets WHERE is_favorite != 0")
            }

            # Determine which rows need updates
            to_remove_normalized = current_favs_normalized - featured_normalized_set
            to_add_normalized = featured_normalized_set - current_favs_normalized

            # Apply updates
            if to_remove_normalized:
                to_remove_original = [
                    all_rels_map[n] for n in to_remove_normalized if n in all_rels_map
                ]
                conn.executemany(
                    "UPDATE assets SET is_favorite = 0 WHERE rel = ?",
                    [(r,) for r in to_remove_original],
                )

            if to_add_normalized:
                to_add_original = [
                    all_rels_map.get(n, input_normalized_map[n]) 
                    for n in to_add_normalized
                ]
                conn.executemany(
                    "UPDATE assets SET is_favorite = 1 WHERE rel = ?",
                    [(r,) for r in to_add_original],
                )
    def update_face_status(self, asset_id: str, status: str) -> None:
        """Update the ``face_status`` for a single asset row."""

        normalized = normalize_face_status(status)
        if normalized is None or not asset_id:
            return
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET face_status = ? WHERE id = ?",
            (normalized, asset_id),
        )
    def update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        """Update the ``face_status`` for multiple assets."""

        normalized = normalize_face_status(status)
        ids_list = [str(asset_id) for asset_id in asset_ids if asset_id]
        if normalized is None or not ids_list:
            return

        placeholders = ", ".join(["?"] * len(ids_list))
        query = f"UPDATE assets SET face_status = ? WHERE id IN ({placeholders})"
        self._db_manager.execute_in_transaction(query, [normalized, *ids_list])
    def set_metadata_version(self, version: int) -> None:
        """Stamp this index with the extraction version that produced its rows."""

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)
        try:
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.commit()
        finally:
            if should_close:
                conn.close()
    def update_location(self, rel: str, location: str) -> None:
        """Update the location string for a single asset."""
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET location = ? WHERE rel = ?",
            (location, rel),
        )
    def clear_location_for_null_gps(self) -> int:
        """Clear persisted location names for assets with (0, 0) GPS coordinates.

        Returns the number of rows affected.
        """
        # The gps column stores a JSON dict like {"lat": 0.0, "lon": 0.0}.
        # We match rows where both lat and lon are effectively zero.
        return self._db_manager.execute_in_transaction(
            """
            UPDATE assets SET location = NULL
            WHERE location IS NOT NULL
              AND gps IS NOT NULL
              AND (
                json_extract(gps, '$.lat') BETWEEN -0.000001 AND 0.000001
                AND json_extract(gps, '$.lon') BETWEEN -0.000001 AND 0.000001
              )
            """,
        )
    def update_asset_geodata(
        self,
        rel: str,
        *,
        gps: Dict[str, float] | None,
        location: str | None,
        metadata_updates: Dict[str, Any] | None = None,
    ) -> None:
        """Atomically update GPS/location columns and JSON metadata for one asset."""

        gps_payload = json.dumps(gps) if gps is not None else None
        with self.transaction() as conn:
            update_parts = ["gps = ?", "location = ?"]
            params: list[Any] = [gps_payload, location]

            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(assets)")
            }
            if "metadata" in columns:
                existing_metadata: Dict[str, Any] = {}
                row = conn.execute(
                    "SELECT metadata FROM assets WHERE rel = ?",
                    (rel,),
                ).fetchone()
                if row is not None and row[0]:
                    try:
                        decoded = json.loads(row[0])
                    except (json.JSONDecodeError, TypeError):
                        decoded = {}
                    if isinstance(decoded, dict):
                        existing_metadata = decoded
                if metadata_updates:
                    existing_metadata.update(
                        _sanitize_metadata_for_json(
                            {
                                key: value
                                for key, value in metadata_updates.items()
                                if value is not None
                            }
                        )
                    )
                if gps is not None:
                    existing_metadata["gps"] = dict(gps)
                else:
                    existing_metadata.pop("gps", None)
                if isinstance(location, str) and location.strip():
                    existing_metadata["location"] = location.strip()
                else:
                    existing_metadata.pop("location", None)
                update_parts.append("metadata = ?")
                params.append(
                    json.dumps(
                        _sanitize_metadata_for_json(existing_metadata),
                        ensure_ascii=False,
                    )
                )

            params.append(rel)
            conn.execute(
                f"UPDATE assets SET {', '.join(update_parts)} WHERE rel = ?",
                params,
            )
    def apply_live_role_updates(
        self,
        updates: List[Tuple[str, int, Optional[str]]],
    ) -> None:
        """Update live_role and live_partner_rel for a batch of assets.
        
        Args:
            updates: List of (rel, live_role, live_partner_rel) tuples.
        """
        if not updates:
            self._db_manager.execute_in_transaction(
                "UPDATE assets SET live_role = 0, live_partner_rel = NULL"
            )
            return

        with self.transaction() as conn:
            conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
            query = "UPDATE assets SET live_role = ?, live_partner_rel = ? WHERE rel = ?"
            params = [(role, partner, rel) for rel, role, partner in updates]
            conn.executemany(query, params)
    def apply_live_role_updates_for_prefix(
        self,
        prefix: str,
        updates: List[Tuple[str, int, Optional[str]]],
    ) -> None:
        """Update live_role/live_partner_rel for assets under *prefix* only."""
        prefix = prefix.rstrip("/")
        prefix_like = f"{prefix}/%"
        with self.transaction() as conn:
            conn.execute(
                "UPDATE assets SET live_role = 0, live_partner_rel = NULL "
                "WHERE rel LIKE ?",
                (prefix_like,),
            )
            query = "UPDATE assets SET live_role = ?, live_partner_rel = ? WHERE rel = ?"
            params = [(role, partner, rel) for rel, role, partner in updates]
            conn.executemany(query, params)
    def update_still_image_times(
        self,
        updates: List[Tuple[str, float]],
    ) -> None:
        """Write still_image_time for still-image rows identified by rel."""
        if not updates:
            return
        with self.transaction() as conn:
            conn.executemany(
                "UPDATE assets SET still_image_time = ? WHERE rel = ?",
                [(t, rel) for rel, t in updates],
            )
    def _insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: Iterable[Dict[str, Any]],
    ) -> None:
        """Helper to bulk insert rows."""
        insert_rows(conn, rows)
    def _row_to_db_params(self, row: Dict[str, Any]) -> List[Any]:
        """Map a dictionary row to a list of values for the DB."""
        return row_to_db_params(row)


__all__ = ["AssetWriteMixin"]
