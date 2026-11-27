"""Manual faces, face keys and person covers for the People store.

Split out of ``FaceStateRepository`` (1922 lines at the time): manual-face CRUD,
the face-key maps that drive rescans, and the cover thumbnails each person card
shows. The bodies moved verbatim; ``_connect`` and the schema stay in the base
class, so the mixin only reads through ``self``.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable

import numpy as np

from .records import ManualFaceRecord, PersonCoverRecord, PersonRecord
from .repository_utils import (
    _deserialize_embedding,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
    profile_state_for_sample_count,
)


class FaceStateMixin:
    """Manual faces, rejected/assigned face keys, and person covers."""

    def get_manual_faces(self) -> list[ManualFaceRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    thumbnail_path, person_id, created_at, image_width, image_height
                FROM manual_faces
                ORDER BY created_at ASC, face_id ASC
                """
            ).fetchall()
        return [self._manual_face_from_row(row) for row in rows]

    def get_manual_face(self, face_id: str) -> ManualFaceRecord | None:
        if not face_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    thumbnail_path, person_id, created_at, image_width, image_height
                FROM manual_faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
        return self._manual_face_from_row(row) if row is not None else None

    def get_manual_faces_for_asset(self, asset_id: str) -> list[ManualFaceRecord]:
        if not asset_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    thumbnail_path, person_id, created_at, image_width, image_height
                FROM manual_faces
                WHERE asset_id = ?
                ORDER BY box_x ASC, box_y ASC, face_id ASC
                """,
                (asset_id,),
            ).fetchall()
        return [self._manual_face_from_row(row) for row in rows]

    def get_manual_faces_for_persons(
        self,
        person_ids: Iterable[str],
    ) -> list[ManualFaceRecord]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return []
        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    face_id, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    thumbnail_path, person_id, created_at, image_width, image_height
                FROM manual_faces
                WHERE person_id IN ({placeholders})
                ORDER BY created_at ASC, face_id ASC
                """,
                unique_ids,
            ).fetchall()
        return [self._manual_face_from_row(row) for row in rows]

    def get_manual_person_ids_for_asset_ids(self, asset_ids: Iterable[str]) -> set[str]:
        unique_ids = [str(asset_id) for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not unique_ids:
            return set()
        self.initialize()
        chunk_size = 900
        person_ids: set[str] = set()
        with closing(self._connect()) as conn:
            for start in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[start : start + chunk_size]
                placeholders = ", ".join(["?"] * len(chunk))
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT person_id
                    FROM manual_faces
                    WHERE asset_id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                person_ids.update(str(row["person_id"]) for row in rows if row["person_id"])
        return person_ids

    def upsert_manual_face(self, face: ManualFaceRecord) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO manual_faces (
                    face_id, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    thumbnail_path, person_id, created_at, image_width, image_height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(face_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    box_x = excluded.box_x,
                    box_y = excluded.box_y,
                    box_w = excluded.box_w,
                    box_h = excluded.box_h,
                    thumbnail_path = excluded.thumbnail_path,
                    person_id = excluded.person_id,
                    created_at = excluded.created_at,
                    image_width = excluded.image_width,
                    image_height = excluded.image_height
                """,
                (
                    face.face_id,
                    face.asset_id,
                    face.asset_rel,
                    face.box_x,
                    face.box_y,
                    face.box_w,
                    face.box_h,
                    face.thumbnail_path,
                    face.person_id,
                    face.created_at,
                    face.image_width,
                    face.image_height,
                ),
            )
            conn.commit()

    def add_manual_face(
        self,
        face: ManualFaceRecord,
        *,
        person_name: str | None = None,
    ) -> None:
        self.initialize()
        timestamp = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO manual_faces (
                    face_id, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    thumbnail_path, person_id, created_at, image_width, image_height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(face_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    box_x = excluded.box_x,
                    box_y = excluded.box_y,
                    box_w = excluded.box_w,
                    box_h = excluded.box_h,
                    thumbnail_path = excluded.thumbnail_path,
                    person_id = excluded.person_id,
                    created_at = excluded.created_at,
                    image_width = excluded.image_width,
                    image_height = excluded.image_height
                """,
                (
                    face.face_id,
                    face.asset_id,
                    face.asset_rel,
                    face.box_x,
                    face.box_y,
                    face.box_w,
                    face.box_h,
                    face.thumbnail_path,
                    face.person_id,
                    face.created_at,
                    face.image_width,
                    face.image_height,
                ),
            )
            if person_name is not None:
                conn.execute(
                    """
                    INSERT INTO person_profiles (
                        person_id, name, center_embedding, embedding_dim,
                        created_at, updated_at, sample_count, profile_state
                    ) VALUES (?, ?, ?, 0, ?, ?, 0, 'unstable')
                    ON CONFLICT(person_id) DO UPDATE SET
                        name = COALESCE(excluded.name, person_profiles.name),
                        updated_at = excluded.updated_at
                    """,
                    (
                        face.person_id,
                        _normalize_name(person_name),
                        sqlite3.Binary(b""),
                        face.created_at,
                        timestamp,
                    ),
                )
            conn.execute(
                """
                INSERT INTO person_covers (
                    person_id, face_id, face_key, asset_id, thumbnail_path, is_custom, updated_at
                ) VALUES (?, ?, NULL, ?, ?, 1, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    face_id = excluded.face_id,
                    face_key = excluded.face_key,
                    asset_id = excluded.asset_id,
                    thumbnail_path = excluded.thumbnail_path,
                    is_custom = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    face.person_id,
                    face.face_id,
                    face.asset_id,
                    face.thumbnail_path,
                    timestamp,
                ),
            )
            row = conn.execute(
                "SELECT sort_order FROM person_card_orders WHERE person_id = ?",
                (face.person_id,),
            ).fetchone()
            if row is None:
                max_order = conn.execute(
                    "SELECT MAX(sort_order) AS max_order FROM person_card_orders"
                ).fetchone()
                next_order = int(max_order["max_order"]) + 1 if max_order and max_order["max_order"] is not None else 0
                conn.execute(
                    """
                    INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (face.person_id, next_order, timestamp),
                )
            conn.commit()

    def delete_manual_face(self, face_id: str) -> None:
        if not face_id:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM manual_faces WHERE face_id = ?", (face_id,))
            conn.commit()

    def move_manual_face(self, face_id: str, target_person_id: str) -> bool:
        if not face_id or not target_person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM manual_faces WHERE face_id = ?",
                (face_id,),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE manual_faces SET person_id = ? WHERE face_id = ?",
                (target_person_id, face_id),
            )
            conn.commit()
        return True

    def get_face_key_map(self, face_keys: Iterable[str]) -> dict[str, str]:
        unique_face_keys = [face_key for face_key in dict.fromkeys(face_keys) if face_key]
        if not unique_face_keys:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_face_keys))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT face_key, person_id
                FROM face_keys
                WHERE face_key IN ({placeholders})
                """,
                unique_face_keys,
            ).fetchall()
        return {row["face_key"]: row["person_id"] for row in rows}

    def get_rejected_face_keys(self, face_keys: Iterable[str]) -> set[str]:
        unique_face_keys = [face_key for face_key in dict.fromkeys(face_keys) if face_key]
        if not unique_face_keys:
            return set()

        self.initialize()
        chunk_size = 900
        rejected: set[str] = set()
        with closing(self._connect()) as conn:
            for start in range(0, len(unique_face_keys), chunk_size):
                chunk = unique_face_keys[start : start + chunk_size]
                placeholders = ", ".join(["?"] * len(chunk))
                rows = conn.execute(
                    f"""
                    SELECT face_key
                    FROM rejected_face_keys
                    WHERE face_key IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                rejected.update(str(row["face_key"]) for row in rows if row["face_key"])
        return rejected

    def reject_face_key(self, face_key: str, *, asset_id: str, asset_rel: str) -> None:
        if not face_key:
            return

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO rejected_face_keys (
                    face_key, asset_id, asset_rel, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(face_key) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    updated_at = excluded.updated_at
                """,
                (face_key, asset_id, asset_rel, updated_at),
            )
            conn.execute("DELETE FROM face_keys WHERE face_key = ?", (face_key,))
            conn.commit()

    def assign_face_key(
        self,
        face_key: str,
        person_id: str,
        *,
        asset_id: str,
        asset_rel: str,
    ) -> None:
        if not face_key or not person_id:
            return

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM rejected_face_keys WHERE face_key = ?", (face_key,))
            conn.execute(
                """
                INSERT INTO face_keys (
                    face_key, person_id, asset_id, asset_rel, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(face_key) DO UPDATE SET
                    person_id = excluded.person_id,
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    updated_at = excluded.updated_at
                """,
                (face_key, person_id, asset_id, asset_rel, updated_at),
            )
            conn.commit()

    def sync_scan_results(self, persons: list[PersonRecord], faces: list[FaceRecord]) -> None:
        self.initialize()
        person_rows = []
        for person in persons:
            sample_count = max(int(person.sample_count), int(person.face_count))
            person_rows.append(
                (
                    person.person_id,
                    _normalize_name(person.name),
                    _serialize_embedding(person.center_embedding),
                    int(person.center_embedding.shape[0]),
                    person.created_at,
                    person.updated_at,
                    sample_count,
                    profile_state_for_sample_count(sample_count),
                )
            )
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = COALESCE(excluded.name, person_profiles.name),
                    center_embedding = excluded.center_embedding,
                    embedding_dim = excluded.embedding_dim,
                    updated_at = excluded.updated_at,
                    sample_count = excluded.sample_count,
                    profile_state = excluded.profile_state
                """,
                person_rows,
            )
            timestamp = _utc_now_iso()
            conn.executemany(
                """
                INSERT INTO face_keys (
                    face_key, person_id, asset_id, asset_rel, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(face_key) DO UPDATE SET
                    person_id = excluded.person_id,
                    asset_id = excluded.asset_id,
                    asset_rel = excluded.asset_rel,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        face.face_key,
                        face.person_id,
                        face.asset_id,
                        face.asset_rel,
                        timestamp,
                    )
                    for face in faces
                    if face.face_key and face.person_id
                ],
            )
            manual_person_rows = conn.execute(
                """
                SELECT DISTINCT person_id
                FROM manual_faces
                """
            ).fetchall()
            ordered_ids = _unique_person_ids(
                [
                    *(person.person_id for person in persons if person.person_id),
                    *(str(row["person_id"]) for row in manual_person_rows if row["person_id"]),
                ]
            )
            if ordered_ids:
                placeholders = ", ".join(["?"] * len(ordered_ids))
                rows = conn.execute(
                    f"""
                    SELECT person_id, sort_order
                    FROM person_card_orders
                    WHERE person_id IN ({placeholders})
                    """,
                    ordered_ids,
                ).fetchall()
                existing_order = {
                    str(row["person_id"]): int(row["sort_order"])
                    for row in rows
                    if row["person_id"] is not None and row["sort_order"] is not None
                }
                next_ids = [person_id for person_id, _order in sorted(existing_order.items(), key=lambda item: item[1])]
                next_ids.extend(person_id for person_id in ordered_ids if person_id not in existing_order)
                conn.executemany(
                    """
                    INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        sort_order = excluded.sort_order,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (person_id, index, timestamp)
                        for index, person_id in enumerate(next_ids)
                    ],
                )
                conn.execute(
                    f"DELETE FROM person_card_orders WHERE person_id NOT IN ({placeholders})",
                    ordered_ids,
                )
            else:
                conn.execute("DELETE FROM person_card_orders")
            conn.commit()

    def get_person_cover_thumbnail_map(self, person_ids: Iterable[str]) -> dict[str, str]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id, thumbnail_path
                FROM person_covers
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {
            str(row["person_id"]): str(row["thumbnail_path"])
            for row in rows
            if row["person_id"] and row["thumbnail_path"]
        }

    def get_person_cover(self, person_id: str) -> PersonCoverRecord | None:
        if not person_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT person_id, face_id, face_key, asset_id, thumbnail_path, is_custom
                FROM person_covers
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()
        if row is None:
            return None
        return PersonCoverRecord(
            person_id=str(row["person_id"]),
            face_id=str(row["face_id"]) if row["face_id"] else None,
            face_key=str(row["face_key"]) if row["face_key"] else None,
            asset_id=str(row["asset_id"]) if row["asset_id"] else None,
            thumbnail_path=str(row["thumbnail_path"]) if row["thumbnail_path"] else None,
            is_custom=bool(int(row["is_custom"] or 0)),
        )

    def sync_person_cover_defaults(
        self,
        cover_rows: Iterable[tuple[str, str | None, str | None, str | None, str | None]],
    ) -> None:
        rows = [
            (
                str(person_id),
                str(face_id) if face_id else None,
                str(face_key) if face_key else None,
                str(asset_id) if asset_id else None,
                str(thumbnail_path) if thumbnail_path else None,
                _utc_now_iso(),
            )
            for person_id, face_id, face_key, asset_id, thumbnail_path in cover_rows
            if person_id
        ]
        if not rows:
            return

        self.initialize()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_covers (
                    person_id, face_id, face_key, asset_id, thumbnail_path, is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    face_id = excluded.face_id,
                    face_key = excluded.face_key,
                    asset_id = excluded.asset_id,
                    thumbnail_path = excluded.thumbnail_path,
                    updated_at = excluded.updated_at
                WHERE person_covers.is_custom = 0
                    AND (
                        COALESCE(person_covers.face_id, '') != COALESCE(excluded.face_id, '')
                        OR COALESCE(person_covers.face_key, '') != COALESCE(excluded.face_key, '')
                        OR COALESCE(person_covers.asset_id, '') != COALESCE(excluded.asset_id, '')
                        OR COALESCE(person_covers.thumbnail_path, '')
                            != COALESCE(excluded.thumbnail_path, '')
                    )
                """,
                rows,
            )
            conn.commit()

    def set_person_cover(
        self,
        person_id: str,
        *,
        face_id: str | None,
        face_key: str | None,
        asset_id: str | None,
        thumbnail_path: str | None,
    ) -> None:
        if not person_id:
            return

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_covers (
                    person_id, face_id, face_key, asset_id, thumbnail_path, is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    face_id = excluded.face_id,
                    face_key = excluded.face_key,
                    asset_id = excluded.asset_id,
                    thumbnail_path = excluded.thumbnail_path,
                    is_custom = 1,
                    updated_at = excluded.updated_at
                """,
                (person_id, face_id, face_key, asset_id, thumbnail_path, updated_at),
            )
            conn.commit()

    def delete_person_cover(self, person_id: str) -> None:
        if not person_id:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM person_covers WHERE person_id = ?", (person_id,))
            conn.commit()

    @staticmethod
    def _manual_face_from_row(row: sqlite3.Row) -> ManualFaceRecord:
        return ManualFaceRecord(
            face_id=str(row["face_id"]),
            asset_id=str(row["asset_id"]),
            asset_rel=str(row["asset_rel"]),
            box_x=int(row["box_x"]),
            box_y=int(row["box_y"]),
            box_w=int(row["box_w"]),
            box_h=int(row["box_h"]),
            thumbnail_path=str(row["thumbnail_path"]) if row["thumbnail_path"] else None,
            person_id=str(row["person_id"]),
            created_at=str(row["created_at"]),
            image_width=int(row["image_width"]),
            image_height=int(row["image_height"]),
        )


__all__ = ["FaceStateMixin"]
