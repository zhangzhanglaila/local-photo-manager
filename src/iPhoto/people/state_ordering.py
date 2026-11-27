"""Sidebar ordering and hidden flags for people and groups.

Split out of ``FaceStateRepository``. These rows are pure user preferences; they
share nothing with the face/profile data beyond the database connection.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable

from .repository_utils import _unique_person_ids, _utc_now_iso


class OrderingStateMixin:
    """Persist the drag order and the hidden flag of people and groups."""

    def get_person_order_map(self, person_ids: Iterable[str]) -> dict[str, int]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id, sort_order
                FROM person_card_orders
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {
            str(row["person_id"]): int(row["sort_order"])
            for row in rows
            if row["person_id"] is not None and row["sort_order"] is not None
        }

    def set_person_order(self, person_ids: Iterable[str]) -> None:
        ordered_ids = _unique_person_ids(person_ids)
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO person_card_orders (person_id, sort_order, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                [
                    (person_id, index, updated_at)
                    for index, person_id in enumerate(ordered_ids)
                ],
            )
            if ordered_ids:
                placeholders = ", ".join(["?"] * len(ordered_ids))
                conn.execute(
                    f"DELETE FROM person_card_orders WHERE person_id NOT IN ({placeholders})",
                    ordered_ids,
                )
            else:
                conn.execute("DELETE FROM person_card_orders")
            conn.commit()

    def set_group_order(self, group_ids: Iterable[str]) -> None:
        ordered_ids = [str(group_id) for group_id in dict.fromkeys(group_ids) if group_id]
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO group_card_orders (group_id, sort_order, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                [
                    (group_id, index, updated_at)
                    for index, group_id in enumerate(ordered_ids)
                ],
            )
            if ordered_ids:
                placeholders = ", ".join(["?"] * len(ordered_ids))
                conn.execute(
                    f"DELETE FROM group_card_orders WHERE group_id NOT IN ({placeholders})",
                    ordered_ids,
                )
            else:
                conn.execute("DELETE FROM group_card_orders")
            conn.commit()

    def get_person_hidden_map(self, person_ids: Iterable[str]) -> dict[str, bool]:
        unique_ids = _unique_person_ids(person_ids)
        if not unique_ids:
            return {}

        self.initialize()
        placeholders = ", ".join(["?"] * len(unique_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT person_id
                FROM hidden_people
                WHERE person_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        return {str(row["person_id"]): True for row in rows if row["person_id"] is not None}

    def is_person_hidden(self, person_id: str) -> bool:
        if not person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM hidden_people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return row is not None

    def set_person_hidden(self, person_id: str, hidden: bool) -> None:
        if not person_id:
            return
        self.initialize()
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            if hidden:
                conn.execute(
                    """
                    INSERT INTO hidden_people (person_id, updated_at)
                    VALUES (?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        updated_at = excluded.updated_at
                    """,
                    (person_id, updated_at),
                )
            else:
                conn.execute("DELETE FROM hidden_people WHERE person_id = ?", (person_id,))
            conn.commit()


__all__ = ["OrderingStateMixin"]
