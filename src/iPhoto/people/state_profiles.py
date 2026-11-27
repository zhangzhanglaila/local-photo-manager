"""Person profiles, renames, merges and per-person state for the People store.

Split out of ``FaceStateRepository`` when it passed 1900 lines. ``merge_persons``
and its helpers move together: the merge rewrites profiles, groups and covers in
one transaction, so splitting them apart would only spread one invariant over
three modules. Bodies moved verbatim.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable

import numpy as np

from .records import PersonProfile, PersonRecord
from .repository_utils import (
    _deserialize_embedding,
    _group_member_key,
    _normalize_name,
    _serialize_embedding,
    _unique_person_ids,
    _utc_now_iso,
    profile_state_for_sample_count,
)


class ProfileStateMixin:
    """Read and edit person profiles; merge people; drop per-person state."""

    def get_profiles(self) -> list[PersonProfile]:
        self.initialize()
        with closing(self._connect()) as conn:
            inferred_sample_counts = {
                str(row["person_id"]): int(row["sample_count"] or 0)
                for row in conn.execute(
                    """
                    SELECT person_id, COUNT(*) AS sample_count
                    FROM face_keys
                    GROUP BY person_id
                    """
                ).fetchall()
            }
            rows = conn.execute("""
                SELECT
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at,
                    sample_count,
                    profile_state
                FROM person_profiles
                """).fetchall()
        profiles: list[PersonProfile] = []
        for row in rows:
            person_id = str(row["person_id"])
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                sample_count = inferred_sample_counts.get(person_id, 0)
            profiles.append(
                PersonProfile(
                    person_id=person_id,
                    name=row["name"],
                    center_embedding=_deserialize_embedding(
                        row["center_embedding"],
                        int(row["embedding_dim"]),
                    ),
                    embedding_dim=int(row["embedding_dim"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    sample_count=sample_count,
                    profile_state=profile_state_for_sample_count(sample_count),
                )
            )
        return profiles

    def get_profile(self, person_id: str) -> PersonProfile | None:
        """Return the profile for a single person, or None if not found."""
        if not person_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    person_id,
                    name,
                    center_embedding,
                    embedding_dim,
                    created_at,
                    updated_at,
                    sample_count,
                    profile_state
                FROM person_profiles
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()
            if row is None:
                return None
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                inferred_row = conn.execute(
                    """
                    SELECT COUNT(*) AS sample_count
                    FROM face_keys
                    WHERE person_id = ?
                    """,
                    (person_id,),
                ).fetchone()
                if inferred_row is not None:
                    sample_count = int(inferred_row["sample_count"] or 0)
        return PersonProfile(
            person_id=str(row["person_id"]),
            name=row["name"],
            center_embedding=_deserialize_embedding(
                row["center_embedding"],
                int(row["embedding_dim"]),
            ),
            embedding_dim=int(row["embedding_dim"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sample_count=sample_count,
            profile_state=profile_state_for_sample_count(sample_count),
        )

    def get_profile_name_map(self, person_ids: Iterable[str]) -> dict[str, str | None]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}
        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id, name
                FROM person_profiles
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {str(row["person_id"]): row["name"] for row in rows if row["person_id"]}

    def rename_person(self, person_id: str, name_or_none: str | None) -> None:
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO person_profiles (
                    person_id, name, center_embedding, embedding_dim,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (
                    person_id,
                    _normalize_name(name_or_none),
                    sqlite3.Binary(b""),
                    0,
                    updated_at,
                    updated_at,
                    0,
                    "unstable",
                ),
            )
            conn.commit()

    def upsert_person_profile(
        self,
        person_id: str,
        *,
        name_or_none: str | None,
        created_at: str | None = None,
        center_embedding: np.ndarray | None = None,
        sample_count: int = 0,
    ) -> None:
        if not person_id:
            return

        self.initialize()
        timestamp = _utc_now_iso()
        normalized_name = _normalize_name(name_or_none)
        embedding = (
            center_embedding
            if center_embedding is not None
            else np.empty((0,), dtype=np.float32)
        )
        created_value = str(created_at or timestamp)
        profile_sample_count = max(int(sample_count), 0)
        with closing(self._connect()) as conn:
            conn.execute(
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
                (
                    person_id,
                    normalized_name,
                    _serialize_embedding(embedding),
                    int(embedding.shape[0]),
                    created_value,
                    timestamp,
                    profile_sample_count,
                    profile_state_for_sample_count(profile_sample_count),
                ),
            )
            conn.commit()

    def merge_persons(
        self,
        source_person_id: str,
        target_person_id: str,
        *,
        center_embedding: np.ndarray,
        target_name: str | None,
        target_created_at: str,
        sample_count: int,
        hidden_state: bool,
    ) -> dict[str, str | None]:
        self.initialize()
        updated_at = _utc_now_iso()
        group_redirects: dict[str, str | None] = {}
        with closing(self._connect()) as conn:
            conn.execute(
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
                (
                    target_person_id,
                    _normalize_name(target_name),
                    _serialize_embedding(center_embedding),
                    int(center_embedding.shape[0]),
                    target_created_at,
                    updated_at,
                    int(sample_count),
                    profile_state_for_sample_count(sample_count),
                ),
            )
            conn.execute(
                "UPDATE face_keys SET person_id = ?, updated_at = ? WHERE person_id = ?",
                (target_person_id, updated_at, source_person_id),
            )
            conn.execute(
                "UPDATE manual_faces SET person_id = ? WHERE person_id = ?",
                (target_person_id, source_person_id),
            )
            source_cover = conn.execute(
                """
                SELECT face_id, face_key, asset_id, thumbnail_path, is_custom
                FROM person_covers
                WHERE person_id = ?
                """,
                (source_person_id,),
            ).fetchone()
            target_cover = conn.execute(
                """
                SELECT is_custom
                FROM person_covers
                WHERE person_id = ?
                """,
                (target_person_id,),
            ).fetchone()
            if (
                source_cover is not None
                and int(source_cover["is_custom"]) == 1
                and (target_cover is None or int(target_cover["is_custom"]) == 0)
            ):
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
                    (
                        target_person_id,
                        source_cover["face_id"],
                        source_cover["face_key"],
                        source_cover["asset_id"],
                        source_cover["thumbnail_path"],
                        updated_at,
                    ),
                )
            conn.execute("DELETE FROM person_covers WHERE person_id = ?", (source_person_id,))
            source_order = conn.execute(
                """
                SELECT sort_order
                FROM person_card_orders
                WHERE person_id = ?
                """,
                (source_person_id,),
            ).fetchone()
            target_order = conn.execute(
                """
                SELECT sort_order
                FROM person_card_orders
                WHERE person_id = ?
                """,
                (target_person_id,),
            ).fetchone()
            merged_order = None
            if target_order is not None:
                merged_order = int(target_order["sort_order"])
            elif source_order is not None:
                merged_order = int(source_order["sort_order"])
            if merged_order is not None:
                conn.execute(
                    """
                    INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        sort_order = excluded.sort_order,
                        updated_at = excluded.updated_at
                    """,
                    (target_person_id, merged_order, updated_at),
                )
            conn.execute("DELETE FROM person_card_orders WHERE person_id = ?", (source_person_id,))
            if hidden_state:
                conn.execute(
                    """
                    INSERT INTO hidden_people (person_id, updated_at)
                    VALUES (?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        updated_at = excluded.updated_at
                    """,
                    (target_person_id, updated_at),
                )
            else:
                conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (target_person_id,))
            conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (source_person_id,))
            conn.execute("DELETE FROM person_profiles WHERE person_id = ?", (source_person_id,))
            group_redirects = self._remap_groups_for_merged_person(
                conn,
                source_person_id=source_person_id,
                target_person_id=target_person_id,
                updated_at=updated_at,
            )
            conn.commit()
        return group_redirects

    def _remap_groups_for_merged_person(
        self,
        conn: sqlite3.Connection,
        *,
        source_person_id: str,
        target_person_id: str,
        updated_at: str,
    ) -> dict[str, str | None]:
        affected = conn.execute(
            """
            SELECT DISTINCT group_id
            FROM people_group_members
            WHERE person_id IN (?, ?)
            ORDER BY group_id ASC
            """,
            (source_person_id, target_person_id),
        ).fetchall()
        if not affected:
            return {}

        redirects: dict[str, str | None] = {}
        for row in affected:
            group_id = str(row["group_id"])
            group = self._group_from_id(conn, group_id)
            if group is None:
                continue
            next_members = _unique_person_ids(
                target_person_id if person_id == source_person_id else person_id
                for person_id in group.member_person_ids
            )
            if len(next_members) < 2:
                self._delete_group(conn, group_id)
                redirects[group_id] = None
                continue

            next_member_key = _group_member_key(next_members)
            if next_member_key == group.member_key:
                continue

            duplicate = conn.execute(
                """
                SELECT group_id
                FROM people_groups
                WHERE member_key = ? AND group_id != ?
                """,
                (next_member_key, group_id),
            ).fetchone()
            if duplicate is not None:
                target_group_id = str(duplicate["group_id"])
                self._transfer_group_cover_if_needed(
                    conn,
                    source_group_id=group_id,
                    target_group_id=target_group_id,
                    updated_at=updated_at,
                )
                self._delete_group(conn, group_id)
                redirects[group_id] = target_group_id
                continue

            conn.execute(
                """
                UPDATE people_groups
                SET member_key = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (next_member_key, updated_at, group_id),
            )
            conn.execute("DELETE FROM people_group_members WHERE group_id = ?", (group_id,))
            conn.executemany(
                """
                INSERT INTO people_group_members (group_id, person_id, position)
                VALUES (?, ?, ?)
                """,
                [(group_id, person_id, index) for index, person_id in enumerate(next_members)],
            )
        return redirects

    def _transfer_group_cover_if_needed(
        self,
        conn: sqlite3.Connection,
        *,
        source_group_id: str,
        target_group_id: str,
        updated_at: str,
    ) -> None:
        source_cache = conn.execute(
            """
            SELECT asset_count, cover_asset_id, cover_is_custom
            FROM people_group_asset_cache
            WHERE group_id = ?
            """,
            (source_group_id,),
        ).fetchone()
        if source_cache is None or int(source_cache["cover_is_custom"] or 0) != 1:
            return

        target_cache = conn.execute(
            """
            SELECT asset_count, cover_is_custom
            FROM people_group_asset_cache
            WHERE group_id = ?
            """,
            (target_group_id,),
        ).fetchone()
        if target_cache is not None and int(target_cache["cover_is_custom"] or 0) == 1:
            return

        asset_count = int(target_cache["asset_count"]) if target_cache is not None else 0
        conn.execute(
            """
            INSERT INTO people_group_asset_cache (
                group_id, asset_count, cover_asset_id, cover_is_custom, updated_at
            ) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                asset_count = excluded.asset_count,
                cover_asset_id = excluded.cover_asset_id,
                cover_is_custom = 1,
                updated_at = excluded.updated_at
            """,
            (
                target_group_id,
                asset_count,
                source_cache["cover_asset_id"],
                updated_at,
            ),
        )

    def _remove_person_from_groups(
        self,
        conn: sqlite3.Connection,
        *,
        person_id: str,
        updated_at: str,
    ) -> dict[str, str | None]:
        affected = conn.execute(
            """
            SELECT DISTINCT group_id
            FROM people_group_members
            WHERE person_id = ?
            ORDER BY group_id ASC
            """,
            (person_id,),
        ).fetchall()
        if not affected:
            return {}

        redirects: dict[str, str | None] = {}
        for row in affected:
            group_id = str(row["group_id"])
            group = self._group_from_id(conn, group_id)
            if group is None:
                continue
            next_members = _unique_person_ids(
                member_id
                for member_id in group.member_person_ids
                if member_id != person_id
            )
            if len(next_members) < 2:
                self._delete_group(conn, group_id)
                redirects[group_id] = None
                continue

            next_member_key = _group_member_key(next_members)
            if next_member_key == group.member_key:
                continue

            duplicate = conn.execute(
                """
                SELECT group_id
                FROM people_groups
                WHERE member_key = ? AND group_id != ?
                """,
                (next_member_key, group_id),
            ).fetchone()
            if duplicate is not None:
                target_group_id = str(duplicate["group_id"])
                self._transfer_group_cover_if_needed(
                    conn,
                    source_group_id=group_id,
                    target_group_id=target_group_id,
                    updated_at=updated_at,
                )
                self._delete_group(conn, group_id)
                redirects[group_id] = target_group_id
                continue

            conn.execute(
                """
                UPDATE people_groups
                SET member_key = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (next_member_key, updated_at, group_id),
            )
            conn.execute("DELETE FROM people_group_members WHERE group_id = ?", (group_id,))
            conn.executemany(
                """
                INSERT INTO people_group_members (group_id, person_id, position)
                VALUES (?, ?, ?)
                """,
                [(group_id, member_id, index) for index, member_id in enumerate(next_members)],
            )
        return redirects

    def delete_person_state(self, person_id: str) -> None:
        if not person_id:
            return

        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM person_covers WHERE person_id = ?", (person_id,))
            conn.execute("DELETE FROM person_card_orders WHERE person_id = ?", (person_id,))
            conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (person_id,))
            conn.execute("DELETE FROM person_profiles WHERE person_id = ?", (person_id,))
            conn.commit()


__all__ = ["ProfileStateMixin"]
