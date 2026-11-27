"""Group state for the People store: creation, membership, assets, covers.

These methods used to sit in ``FaceStateRepository``, which had grown to 1922
lines covering profiles, manual faces, covers, face keys, groups and the schema.
They take the repository as ``self`` (a mixin) so ``_connect`` and the row
mappers stay in one place and every existing call site keeps working.

Nothing here changes behaviour: the method bodies moved verbatim.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable

from .records import PeopleGroupRecord
from .repository_utils import (
    _group_id_for_member_key,
    _group_member_key,
    _unique_person_ids,
    _utc_now_iso,
)


class GroupStateMixin:
    """Group CRUD, membership maintenance and group covers."""

    def create_group(self, member_person_ids: Iterable[str]) -> PeopleGroupRecord | None:
        members = _unique_person_ids(member_person_ids)
        if len(members) < 2:
            return None

        self.initialize()
        member_key = _group_member_key(members)
        timestamp = _utc_now_iso()
        group_id = _group_id_for_member_key(member_key)
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT group_id FROM people_groups WHERE member_key = ?",
                (member_key,),
            ).fetchone()
            if existing is not None:
                return self._group_from_id(conn, str(existing["group_id"]))

            conn.execute(
                """
                INSERT INTO people_groups (group_id, member_key, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (group_id, member_key, timestamp, timestamp),
            )
            conn.executemany(
                """
                INSERT INTO people_group_members (group_id, person_id, position)
                VALUES (?, ?, ?)
                """,
                [(group_id, person_id, index) for index, person_id in enumerate(members)],
            )
            self._ensure_group_order_row(conn, group_id, timestamp)
            conn.commit()
            return self._group_from_id(conn, group_id)

    def list_groups(self) -> list[PeopleGroupRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT group_id
                FROM people_groups
                LEFT JOIN group_card_orders USING (group_id)
                ORDER BY
                    CASE WHEN group_card_orders.sort_order IS NULL THEN 1 ELSE 0 END ASC,
                    group_card_orders.sort_order ASC,
                    people_groups.created_at ASC,
                    people_groups.group_id ASC
                """).fetchall()
            groups: list[PeopleGroupRecord] = []
            for row in rows:
                group = self._group_from_id(conn, str(row["group_id"]))
                if group is not None:
                    groups.append(group)
            return groups

    def list_group_ids_for_people(self, person_ids: Iterable[str]) -> list[str]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return []

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT group_id
                FROM people_group_members
                WHERE person_id IN ({placeholders})
                ORDER BY group_id ASC
                """,
                unique_ids,
            ).fetchall()
        return [str(row["group_id"]) for row in rows if row["group_id"]]

    def get_group(self, group_id: str) -> PeopleGroupRecord | None:
        if not group_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            return self._group_from_id(conn, group_id)

    def delete_group(self, group_id: str) -> PeopleGroupRecord | None:
        if not group_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            group = self._group_from_id(conn, group_id)
            if group is None:
                return None
            self._delete_group(conn, group_id)
            conn.commit()
            return group

    def remove_person_from_groups(self, person_id: str) -> dict[str, str | None]:
        if not person_id:
            return {}

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            redirects = self._remove_person_from_groups(
                conn,
                person_id=person_id,
                updated_at=updated_at,
            )
            conn.commit()
        return redirects

    def has_group_asset_cache(self, group_id: str) -> bool:
        if not group_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM people_group_asset_cache WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return row is not None

    def get_group_asset_ids(self, group_id: str) -> list[str]:
        if not group_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT asset_id
                FROM people_group_assets
                WHERE group_id = ?
                ORDER BY position ASC, asset_id ASC
                """,
                (group_id,),
            ).fetchall()
        return [str(row["asset_id"]) for row in rows if row["asset_id"]]

    def replace_group_assets(
        self,
        group_id: str,
        asset_rows: Iterable[tuple[str, str]],
    ) -> None:
        if not group_id:
            return

        rows = [
            (str(asset_id), str(last_detected_at), index)
            for index, (asset_id, last_detected_at) in enumerate(asset_rows)
            if asset_id
        ]
        self.initialize()
        timestamp = _utc_now_iso()
        asset_ids = {asset_id for asset_id, _last_detected_at, _position in rows}
        fallback_cover_asset_id = rows[0][0] if rows else None
        with closing(self._connect()) as conn:
            group_exists = conn.execute(
                "SELECT 1 FROM people_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if group_exists is None:
                return
            existing_cache = conn.execute(
                """
                SELECT cover_asset_id, cover_is_custom
                FROM people_group_asset_cache
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
            cover_asset_id = fallback_cover_asset_id
            cover_is_custom = 0
            if existing_cache is not None and int(existing_cache["cover_is_custom"]) == 1:
                existing_cover_asset_id = str(existing_cache["cover_asset_id"] or "")
                if existing_cover_asset_id in asset_ids:
                    cover_asset_id = existing_cover_asset_id
                    cover_is_custom = 1

            conn.execute("DELETE FROM people_group_assets WHERE group_id = ?", (group_id,))
            conn.executemany(
                """
                INSERT INTO people_group_assets (
                    group_id, asset_id, position, last_detected_at
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (group_id, asset_id, position, last_detected_at)
                    for asset_id, last_detected_at, position in rows
                ],
            )
            conn.execute(
                """
                INSERT INTO people_group_asset_cache (
                    group_id, asset_count, cover_asset_id, cover_is_custom, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    asset_count = excluded.asset_count,
                    cover_asset_id = excluded.cover_asset_id,
                    cover_is_custom = excluded.cover_is_custom,
                    updated_at = excluded.updated_at
                """,
                (group_id, len(rows), cover_asset_id, cover_is_custom, timestamp),
            )
            conn.commit()

    def get_group_cover_asset_id(self, group_id: str) -> str | None:
        if not group_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT cover_asset_id
                FROM people_group_asset_cache
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
        if row is None or not row["cover_asset_id"]:
            return None
        return str(row["cover_asset_id"])

    def set_group_cover_asset(self, group_id: str, asset_id: str) -> bool:
        if not group_id or not asset_id:
            return False

        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            asset_exists = conn.execute(
                """
                SELECT 1
                FROM people_group_assets
                WHERE group_id = ? AND asset_id = ?
                """,
                (group_id, asset_id),
            ).fetchone()
            if asset_exists is None:
                return False
            count_row = conn.execute(
                """
                SELECT COUNT(*) AS asset_count
                FROM people_group_assets
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
            asset_count = int(count_row["asset_count"]) if count_row is not None else 0
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
                (group_id, asset_count, asset_id, updated_at),
            )
            conn.commit()
        return True

    @staticmethod
    def _ensure_group_order_row(
        conn: sqlite3.Connection,
        group_id: str,
        updated_at: str,
    ) -> None:
        row = conn.execute(
            "SELECT 1 FROM group_card_orders WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if row is not None:
            return
        max_order = conn.execute(
            "SELECT MAX(sort_order) AS max_order FROM group_card_orders"
        ).fetchone()
        next_order = int(max_order["max_order"]) + 1 if max_order and max_order["max_order"] is not None else 0
        conn.execute(
            """
            INSERT INTO group_card_orders (group_id, sort_order, updated_at)
            VALUES (?, ?, ?)
            """,
            (group_id, next_order, updated_at),
        )


__all__ = ["GroupStateMixin"]
