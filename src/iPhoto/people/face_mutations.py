"""Write side of the face index: scans, merges, moves and group membership.

Split out of ``FaceRepository``. This is the half that mutates rows, including the
two merge routines that also rewrite People *state* (hidden flags, order, covers)
through ``state_repository``. Bodies moved verbatim, no SQL changed.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable

import numpy as np

from .records import (
    FaceMutationResult,
    FaceRecord,
    ManualFaceRecord,
    PeopleGroupRecord,
    PersonRecord,
)
from .face_merges import FaceMergeMixin
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


class FaceMutationMixin(FaceMergeMixin):
    """All mutating operations on faces, persons and groups."""

    def replace_all(
        self,
        faces: list[FaceRecord],
        persons: list[PersonRecord],
        *,
        sync_runtime_state: bool = True,
    ) -> None:
        self.initialize()
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM persons")
            conn.execute("DELETE FROM faces")
            person_rows = []
            for person in persons:
                sample_count = max(int(person.sample_count), int(person.face_count))
                person_rows.append(
                    (
                        person.person_id,
                        _normalize_name(person.name),
                        person.key_face_id,
                        person.face_count,
                        _serialize_embedding(person.center_embedding),
                        person.created_at,
                        person.updated_at,
                        sample_count,
                        profile_state_for_sample_count(sample_count),
                    )
                )
            conn.executemany(
                """
                INSERT INTO faces (
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        face.face_id,
                        face.face_key,
                        face.asset_id,
                        face.asset_rel,
                        face.box_x,
                        face.box_y,
                        face.box_w,
                        face.box_h,
                        face.confidence,
                        _serialize_embedding(face.embedding),
                        face.embedding_dim,
                        face.thumbnail_path,
                        face.person_id,
                        face.detected_at,
                        face.image_width,
                        face.image_height,
                    )
                    for face in faces
                ],
            )
            conn.executemany(
                """
                INSERT INTO persons (
                    person_id, name, key_face_id, face_count, center_embedding,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                person_rows,
            )
            conn.commit()
        if sync_runtime_state:
            self.sync_runtime_state()
    def sync_runtime_state(self) -> None:
        if self._state_repo is None:
            return
        self._sync_person_cover_defaults()
        self.refresh_all_group_assets()
    def remove_faces_for_assets(
        self,
        asset_ids: Iterable[str],
        asset_rels: Iterable[str] = (),
    ) -> None:
        self.initialize()
        ids_list = [str(value) for value in asset_ids if value]
        rels_list = [str(value) for value in asset_rels if value]
        clauses: list[str] = []
        params: list[str] = []
        if ids_list:
            placeholders = ", ".join(["?"] * len(ids_list))
            clauses.append(f"asset_id IN ({placeholders})")
            params.extend(ids_list)
        if rels_list:
            placeholders = ", ".join(["?"] * len(rels_list))
            clauses.append(f"asset_rel IN ({placeholders})")
            params.extend(rels_list)
        if not clauses:
            return

        with closing(self._connect()) as conn:
            matched_faces = conn.execute(
                f"""
                SELECT face_id, person_id
                FROM faces
                WHERE {' OR '.join(clauses)}
                """,
                params,
            ).fetchall()
            if not matched_faces:
                return

            affected_person_ids = [
                str(row["person_id"]) for row in matched_faces if row["person_id"]
            ]
            if affected_person_ids:
                placeholders = ", ".join(["?"] * len(affected_person_ids))
                # Runtime person rows are fully rebuilt after rescans, so remove
                # affected clusters first to avoid dangling key_face_id references.
                conn.execute(
                    f"DELETE FROM persons WHERE person_id IN ({placeholders})",
                    affected_person_ids,
                )

            face_ids = [str(row["face_id"]) for row in matched_faces if row["face_id"]]
            placeholders = ", ".join(["?"] * len(face_ids))
            conn.execute(
                f"DELETE FROM faces WHERE face_id IN ({placeholders})",
                face_ids,
            )
            orphaned = {row[0] for row in conn.execute("""
                    SELECT person_id
                    FROM persons
                    WHERE person_id NOT IN (
                        SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL
                    )
                    """).fetchall()}
            if orphaned:
                placeholders = ", ".join(["?"] * len(orphaned))
                conn.execute(
                    f"DELETE FROM persons WHERE person_id IN ({placeholders})", list(orphaned)
                )
            conn.commit()
        if self._state_repo is not None:
            self._sync_person_cover_defaults()
            self.refresh_all_group_assets()
    def set_person_hidden(self, person_id: str, hidden: bool) -> bool:
        if self._state_repo is None or not person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM persons WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        if row is None and not self._state_repo.get_manual_faces_for_persons([person_id]):
            return False
        self._state_repo.set_person_hidden(person_id, hidden)
        return True
    def rename_person(self, person_id: str, name_or_none: str | None) -> None:
        self.initialize()
        normalized_name = _normalize_name(name_or_none)
        updated_at = _utc_now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE persons SET name = ?, updated_at = ? WHERE person_id = ?",
                (normalized_name, updated_at, person_id),
            )
            conn.commit()
        if self._state_repo is not None:
            self._state_repo.rename_person(person_id, normalized_name)
    def set_person_cover(self, person_id: str, face_id: str) -> bool:
        if self._state_repo is None or not person_id or not face_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT face_id, face_key, asset_id, thumbnail_path
                FROM faces
                WHERE person_id = ? AND face_id = ?
                """,
                (person_id, face_id),
            ).fetchone()
        if row is None:
            manual_face = self._state_repo.get_manual_face(face_id)
            if manual_face is None or manual_face.person_id != person_id:
                return False
            self._state_repo.set_person_cover(
                person_id,
                face_id=manual_face.face_id,
                face_key=None,
                asset_id=manual_face.asset_id,
                thumbnail_path=manual_face.thumbnail_path,
            )
            return True
        self._state_repo.set_person_cover(
            person_id,
            face_id=row["face_id"],
            face_key=row["face_key"],
            asset_id=row["asset_id"],
            thumbnail_path=row["thumbnail_path"],
        )
        return True
    def set_person_order(self, person_ids: Iterable[str]) -> None:
        if self._state_repo is None:
            return
        self._state_repo.set_person_order(person_ids)
    def set_group_order(self, group_ids: Iterable[str]) -> None:
        if self._state_repo is None:
            return
        self._state_repo.set_group_order(group_ids)
    def delete_face(self, face_id: str) -> FaceMutationResult | None:
        if not face_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
            if row is not None:
                face = self._face_from_row(row)
                if not face.person_id:
                    return None
                conn.execute("UPDATE faces SET person_id = NULL WHERE face_id = ?", (face_id,))
                conn.commit()
                if self._state_repo is not None:
                    self._state_repo.reject_face_key(
                        face.face_key,
                        asset_id=face.asset_id,
                        asset_rel=face.asset_rel,
                    )
                return self._finalize_face_mutation(
                    changed_asset_ids=(face.asset_id,),
                    changed_person_ids=(face.person_id,),
                )

        if self._state_repo is None:
            return None
        manual_face = self._state_repo.get_manual_face(face_id)
        if manual_face is None:
            return None
        self._state_repo.delete_manual_face(face_id)
        return self._finalize_face_mutation(
            changed_asset_ids=(manual_face.asset_id,),
            changed_person_ids=(manual_face.person_id,),
        )
    def move_face_to_person(
        self,
        face_id: str,
        target_person_id: str,
    ) -> FaceMutationResult | None:
        if not face_id or not target_person_id:
            return None
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
            if row is not None:
                face = self._face_from_row(row)
                if not face.person_id or face.person_id == target_person_id:
                    return None
                conn.execute(
                    "UPDATE faces SET person_id = ? WHERE face_id = ?",
                    (target_person_id, face_id),
                )
                conn.commit()
                if self._state_repo is not None:
                    self._state_repo.assign_face_key(
                        face.face_key,
                        target_person_id,
                        asset_id=face.asset_id,
                        asset_rel=face.asset_rel,
                    )
                return self._finalize_face_mutation(
                    changed_asset_ids=(face.asset_id,),
                    changed_person_ids=(face.person_id, target_person_id),
                )

        if self._state_repo is None:
            return None
        manual_face = self._state_repo.get_manual_face(face_id)
        if manual_face is None or manual_face.person_id == target_person_id:
            return None
        if not self._state_repo.move_manual_face(face_id, target_person_id):
            return None
        return self._finalize_face_mutation(
            changed_asset_ids=(manual_face.asset_id,),
            changed_person_ids=(manual_face.person_id, target_person_id),
        )
    def move_face_to_new_person(
        self,
        face_id: str,
        new_person_id: str,
        new_name: str,
    ) -> FaceMutationResult | None:
        normalized_name = _normalize_name(new_name)
        if not face_id or not new_person_id or not normalized_name:
            return None

        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE face_id = ?
                """,
                (face_id,),
            ).fetchone()
            if row is not None:
                face = self._face_from_row(row)
                if not face.person_id or face.person_id == new_person_id:
                    return None
                conn.execute(
                    "UPDATE faces SET person_id = ? WHERE face_id = ?",
                    (new_person_id, face_id),
                )
                conn.commit()
                if self._state_repo is not None:
                    self._state_repo.assign_face_key(
                        face.face_key,
                        new_person_id,
                        asset_id=face.asset_id,
                        asset_rel=face.asset_rel,
                    )
                    self._state_repo.upsert_person_profile(
                        new_person_id,
                        name_or_none=normalized_name,
                        created_at=face.detected_at,
                        center_embedding=face.embedding,
                        sample_count=1,
                    )
                return self._finalize_face_mutation(
                    changed_asset_ids=(face.asset_id,),
                    changed_person_ids=(face.person_id, new_person_id),
                )

        if self._state_repo is None:
            return None
        manual_face = self._state_repo.get_manual_face(face_id)
        if manual_face is None or manual_face.person_id == new_person_id:
            return None
        self._state_repo.upsert_person_profile(
            new_person_id,
            name_or_none=normalized_name,
            created_at=manual_face.created_at,
        )
        if not self._state_repo.move_manual_face(face_id, new_person_id):
            return None
        return self._finalize_face_mutation(
            changed_asset_ids=(manual_face.asset_id,),
            changed_person_ids=(manual_face.person_id, new_person_id),
        )
    def create_group(self, member_person_ids: Iterable[str]) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        group = self._state_repo.create_group(member_person_ids)
        if group is not None:
            self.refresh_group_assets(group.group_id)
        return group
    def delete_group(self, group_id: str) -> tuple[bool, PeopleGroupRecord | None, list[str]]:
        if self._state_repo is None or not group_id:
            return False, None, []
        self.initialize()
        group = self._state_repo.get_group(group_id)
        if group is None:
            return False, None, []
        asset_ids = self.get_common_asset_ids_for_group(group_id)
        deleted_group = self._state_repo.delete_group(group_id)
        if deleted_group is None:
            return False, None, []
        return True, deleted_group, asset_ids
    def set_group_cover_asset(self, group_id: str, asset_id: str) -> bool:
        if self._state_repo is None:
            return False
        self.initialize()
        if not self._state_repo.has_group_asset_cache(group_id):
            self.refresh_group_assets(group_id)
        return self._state_repo.set_group_cover_asset(group_id, asset_id)
    def refresh_group_assets(self, group_id: str) -> list[str]:
        if self._state_repo is None:
            return []
        group = self.get_group(group_id)
        if group is None:
            return []
        asset_rows = self._common_asset_rows_for_persons(group.member_person_ids)
        self._state_repo.replace_group_assets(group.group_id, asset_rows)
        return [asset_id for asset_id, _last_detected_at in asset_rows]
    def refresh_all_group_assets(self) -> None:
        if self._state_repo is None:
            return
        self.initialize()
        for group in self._state_repo.list_groups():
            self.refresh_group_assets(group.group_id)
    def _sync_person_cover_defaults(self) -> None:
        if self._state_repo is None:
            return
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    persons.person_id,
                    faces.face_id,
                    faces.face_key,
                    faces.asset_id,
                    faces.thumbnail_path
                FROM persons
                LEFT JOIN faces ON faces.face_id = persons.key_face_id
                ORDER BY persons.created_at ASC, persons.person_id ASC
                """).fetchall()
        self._state_repo.sync_person_cover_defaults(
            (
                (
                    str(row["person_id"]),
                    row["face_id"],
                    row["face_key"],
                    row["asset_id"],
                    row["thumbnail_path"],
                )
                for row in rows
            )
        )
    def _finalize_face_mutation(
        self,
        *,
        changed_asset_ids: Iterable[str],
        changed_person_ids: Iterable[str],
    ) -> FaceMutationResult:
        person_ids = tuple(dict.fromkeys(person_id for person_id in changed_person_ids if person_id))
        asset_ids = set(asset_id for asset_id in changed_asset_ids if asset_id)
        group_redirects: dict[str, str | None] = {}
        changed_group_ids: set[str] = set()
        active_person_ids: list[str] = []

        if self._state_repo is not None and person_ids:
            changed_group_ids.update(self._state_repo.list_group_ids_for_people(person_ids))

        for person_id in person_ids:
            if self._rebuild_runtime_person(person_id):
                active_person_ids.append(person_id)
                continue
            if self._state_repo is not None:
                group_redirects.update(self._state_repo.remove_person_from_groups(person_id))
                self._state_repo.delete_person_state(person_id)

        if self._state_repo is not None:
            for person_id in active_person_ids:
                self._repair_person_cover(person_id)
            self._sync_person_cover_defaults()
            remaining_group_ids = set(self._state_repo.list_group_ids_for_people(active_person_ids))
            changed_group_ids.update(remaining_group_ids)
            changed_group_ids.update(group_redirects)
            changed_group_ids.update(group_id for group_id in group_redirects.values() if group_id)
            for group_id in changed_group_ids:
                self.refresh_group_assets(group_id)

        for person_id in active_person_ids:
            asset_ids.update(self.get_asset_ids_by_person(person_id))

        return FaceMutationResult(
            changed_asset_ids=tuple(sorted(asset_ids)),
            changed_person_ids=person_ids,
            changed_group_ids=tuple(sorted(group_id for group_id in changed_group_ids if group_id)),
            group_redirects=group_redirects,
        )
    def _repair_person_cover(self, person_id: str) -> None:
        if self._state_repo is None or not person_id:
            return

        manual_faces = self._state_repo.get_manual_faces_for_persons((person_id,))
        has_auto_faces = self._has_auto_faces(person_id)
        cover = self._state_repo.get_person_cover(person_id)
        if cover is None:
            if manual_faces and not has_auto_faces:
                first_face = manual_faces[0]
                self._state_repo.set_person_cover(
                    person_id,
                    face_id=first_face.face_id,
                    face_key=None,
                    asset_id=first_face.asset_id,
                    thumbnail_path=first_face.thumbnail_path,
                )
            return

        valid_auto_faces = {face.face_id: face for face in self.get_faces_by_asset_id(cover.asset_id or "")}
        valid_manual_faces = {face.face_id: face for face in manual_faces}
        auto_face = valid_auto_faces.get(cover.face_id or "")
        if auto_face is not None and auto_face.person_id == person_id:
            return
        if cover.face_id in valid_manual_faces:
            return

        if valid_manual_faces and not has_auto_faces:
            fallback = next(iter(valid_manual_faces.values()))
            self._state_repo.set_person_cover(
                person_id,
                face_id=fallback.face_id,
                face_key=None,
                asset_id=fallback.asset_id,
                thumbnail_path=fallback.thumbnail_path,
            )
            return
        self._state_repo.delete_person_cover(person_id)
    def _has_auto_faces(self, person_id: str) -> bool:
        if not person_id:
            return False
        self.initialize()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM faces WHERE person_id = ? LIMIT 1",
                (person_id,),
            ).fetchone()
        return row is not None


__all__ = ["FaceMutationMixin"]
