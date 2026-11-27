"""SQLite face index repository for People clusters."""

from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Iterable

import numpy as np

from .records import (
    AssetFaceAnnotation,
    FaceRecord,
    ManualFaceRecord,
    PeopleGroupRecord,
    PersonRecord,
    PersonSummary,
)
from .repository_utils import (
    _deserialize_embedding,
    _key_face_sort_key,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
    compute_cluster_center,
    profile_state_for_sample_count,
)
from .state_repository import FaceStateRepository
from .face_mutations import FaceMutationMixin
from .face_queries import FaceQueryMixin
from ..config import SQLITE_LONG_BUSY_TIMEOUT_S


class FaceRepository(FaceQueryMixin, FaceMutationMixin):
    def __init__(self, db_path: Path, state_db_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._state_repo = FaceStateRepository(state_db_path) if state_db_path is not None else None

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def state_repository(self) -> FaceStateRepository | None:
        return self._state_repo

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._create_schema(conn)
        if self._state_repo is not None:
            self._state_repo.initialize()







































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
            CREATE TABLE IF NOT EXISTS faces (
                face_id TEXT PRIMARY KEY,
                face_key TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                box_x INTEGER NOT NULL,
                box_y INTEGER NOT NULL,
                box_w INTEGER NOT NULL,
                box_h INTEGER NOT NULL,
                confidence REAL NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                thumbnail_path TEXT,
                person_id TEXT,
                detected_at TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                key_face_id TEXT NOT NULL REFERENCES faces(face_id),
                face_count INTEGER NOT NULL,
                center_embedding BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                profile_state TEXT NOT NULL DEFAULT 'unstable'
            )
            """)
        FaceRepository._ensure_column(
            conn,
            "persons",
            "sample_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        FaceRepository._ensure_column(
            conn,
            "persons",
            "profile_state",
            "TEXT NOT NULL DEFAULT 'unstable'",
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_face_key ON faces(face_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_asset_id ON faces(asset_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_asset_rel ON faces(asset_rel)")

    @staticmethod
    def _face_from_row(row: sqlite3.Row) -> FaceRecord:
        return FaceRecord(
            face_id=row["face_id"],
            face_key=row["face_key"],
            asset_id=row["asset_id"],
            asset_rel=row["asset_rel"],
            box_x=int(row["box_x"]),
            box_y=int(row["box_y"]),
            box_w=int(row["box_w"]),
            box_h=int(row["box_h"]),
            confidence=float(row["confidence"]),
            embedding=_deserialize_embedding(row["embedding"], int(row["embedding_dim"])),
            embedding_dim=int(row["embedding_dim"]),
            thumbnail_path=row["thumbnail_path"],
            person_id=row["person_id"],
            detected_at=row["detected_at"],
            image_width=int(row["image_width"]),
            image_height=int(row["image_height"]),
        )

    @staticmethod
    def _person_from_row(row: sqlite3.Row) -> PersonRecord:
        center_blob = row["center_embedding"]
        embedding_dim = int(len(center_blob) / 4) if center_blob else 0
        return PersonRecord(
            person_id=str(row["person_id"]),
            name=row["name"],
            key_face_id=str(row["key_face_id"]),
            face_count=int(row["face_count"]),
            center_embedding=_deserialize_embedding(center_blob, embedding_dim),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            sample_count=int(row["sample_count"] or row["face_count"] or 0),
            profile_state=str(
                row["profile_state"]
                or profile_state_for_sample_count(int(row["sample_count"] or row["face_count"] or 0))
            ),
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
