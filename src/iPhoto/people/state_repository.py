"""SQLite state repository for persisted People user state."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

import numpy as np

from .state_faces import FaceStateMixin
from .state_ordering import OrderingStateMixin
from .state_profiles import ProfileStateMixin
from .state_groups import GroupStateMixin
from .records import (
    FaceRecord,
    ManualFaceRecord,
    PeopleGroupRecord,
    PersonProfile,
    PersonRecord,
)
from .repository_utils import (
    _deserialize_embedding,
    _group_id_for_member_key,
    _group_member_key,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
    profile_state_for_sample_count,
)



#: Schema generation for the People state database. Bump it when a migration runs so
#: the next open can tell whether the file is already up to date.
import logging
from ..config import SQLITE_LONG_BUSY_TIMEOUT_S

LOGGER = logging.getLogger(__name__)

PEOPLE_SCHEMA_VERSION = 1


class FaceStateRepository(GroupStateMixin, FaceStateMixin, ProfileStateMixin, OrderingStateMixin):
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)
            current = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if current < PEOPLE_SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {PEOPLE_SCHEMA_VERSION}")
                conn.commit()























    @staticmethod
    def _delete_group(conn: sqlite3.Connection, group_id: str) -> None:
        conn.execute("DELETE FROM group_card_orders WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM people_groups WHERE group_id = ?", (group_id,))

    @staticmethod
    def _group_from_id(
        conn: sqlite3.Connection,
        group_id: str,
    ) -> PeopleGroupRecord | None:
        row = conn.execute(
            """
            SELECT group_id, member_key, created_at, updated_at
            FROM people_groups
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()
        if row is None:
            return None

        members = conn.execute(
            """
            SELECT person_id
            FROM people_group_members
            WHERE group_id = ?
            ORDER BY position ASC, person_id ASC
            """,
            (group_id,),
        ).fetchall()
        member_person_ids = tuple(str(member["person_id"]) for member in members)
        if len(member_person_ids) < 2:
            return None
        return PeopleGroupRecord(
            group_id=str(row["group_id"]),
            member_person_ids=member_person_ids,
            member_key=str(row["member_key"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=SQLITE_LONG_BUSY_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_profiles (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                center_embedding BLOB,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                profile_state TEXT NOT NULL DEFAULT 'unstable'
            )
            """)
        FaceStateRepository._ensure_column(
            conn,
            "person_profiles",
            "sample_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        FaceStateRepository._ensure_column(
            conn,
            "person_profiles",
            "profile_state",
            "TEXT NOT NULL DEFAULT 'unstable'",
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS face_keys (
                face_key TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_keys_person_id ON face_keys(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_keys_asset_id ON face_keys(asset_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rejected_face_keys (
                face_key TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rejected_face_keys_asset_id "
            "ON rejected_face_keys(asset_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_covers (
                person_id TEXT PRIMARY KEY,
                face_id TEXT,
                face_key TEXT,
                asset_id TEXT,
                thumbnail_path TEXT,
                is_custom INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_covers_face_key " "ON person_covers(face_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_covers_asset_id " "ON person_covers(asset_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_card_orders (
                person_id TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_person_card_orders_sort_order "
            "ON person_card_orders(sort_order)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_card_orders (
                group_id TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_card_orders_sort_order "
            "ON group_card_orders(sort_order)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hidden_people (
                person_id TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            )
            """)
        FaceStateRepository._drop_legacy_manual_faces(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_faces (
                face_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                box_x INTEGER NOT NULL,
                box_y INTEGER NOT NULL,
                box_w INTEGER NOT NULL,
                box_h INTEGER NOT NULL,
                thumbnail_path TEXT,
                person_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_faces_asset_id "
            "ON manual_faces(asset_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_faces_person_id "
            "ON manual_faces(person_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_groups (
                group_id TEXT PRIMARY KEY,
                member_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_group_members (
                group_id TEXT NOT NULL REFERENCES people_groups(group_id) ON DELETE CASCADE,
                person_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (group_id, person_id)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_people_group_members_group_id "
            "ON people_group_members(group_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_people_group_members_person_id "
            "ON people_group_members(person_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_group_asset_cache (
                group_id TEXT PRIMARY KEY REFERENCES people_groups(group_id) ON DELETE CASCADE,
                asset_count INTEGER NOT NULL,
                cover_asset_id TEXT,
                cover_is_custom INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """)
        FaceStateRepository._ensure_column(
            conn,
            "people_group_asset_cache",
            "cover_is_custom",
            "INTEGER NOT NULL DEFAULT 0",
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_group_assets (
                group_id TEXT NOT NULL REFERENCES people_groups(group_id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                last_detected_at TEXT NOT NULL,
                PRIMARY KEY (group_id, asset_id)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_people_group_assets_group_id "
            "ON people_group_assets(group_id)"
        )

    @staticmethod
    def _drop_legacy_manual_faces(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(manual_faces)").fetchall()
        }
        if not columns:
            return
        expected = {
            "face_id",
            "asset_id",
            "asset_rel",
            "box_x",
            "box_y",
            "box_w",
            "box_h",
            "thumbnail_path",
            "person_id",
            "created_at",
            "image_width",
            "image_height",
        }
        legacy = {
            "face_key",
            "confidence",
            "embedding",
            "embedding_dim",
            "detected_at",
            "is_manual",
        }
        if columns & legacy or not expected.issubset(columns):
            # Never drop user data: move the incompatible table aside so manual
            # face annotations remain recoverable, then let the caller recreate an
            # empty table with the current schema.
            from datetime import datetime, timezone

            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup = f"manual_faces_legacy_{suffix}"
            conn.execute(f'ALTER TABLE manual_faces RENAME TO "{backup}"')
            LOGGER.warning(
                "manual_faces used an older schema; preserved as %s", backup
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

