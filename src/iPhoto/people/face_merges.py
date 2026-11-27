"""Person merging for the face index, including the runtime-state rewrite.

``merge_persons_with_redirects`` is the most intricate routine in the People
store: it rewrites faces, persons, groups and the per-person runtime state in one
transaction. It gets its own module so it can be read (and changed) alone.
Bodies moved verbatim from ``face_repository.py``.
"""

from __future__ import annotations

from contextlib import closing
from typing import Iterable

import numpy as np

from .records import FaceRecord, PersonRecord
from .repository_utils import (
    _key_face_sort_key,
    _normalize_name,
    _serialize_embedding,
    _utc_now_iso,
    compute_cluster_center,
    profile_state_for_sample_count,
)


class FaceMergeMixin:
    """Merge two persons and rebuild the surviving person's runtime state."""

    def merge_persons(self, source_person_id: str, target_person_id: str) -> bool:
        merged, _group_redirects = self.merge_persons_with_redirects(
            source_person_id,
            target_person_id,
        )
        return merged
    def merge_persons_with_redirects(
        self,
        source_person_id: str,
        target_person_id: str,
    ) -> tuple[bool, dict[str, str | None]]:
        if not source_person_id or not target_person_id or source_person_id == target_person_id:
            return False, {}
        source_hidden = False
        target_hidden = False
        if self._state_repo is not None:
            hidden_map = self._state_repo.get_person_hidden_map((source_person_id, target_person_id))
            source_hidden = bool(hidden_map.get(source_person_id, False))
            target_hidden = bool(hidden_map.get(target_person_id, False))
            if source_hidden != target_hidden:
                return False, {}

        self.initialize()
        group_redirects: dict[str, str | None] = {}
        with closing(self._connect()) as conn:
            faces = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE person_id IN (?, ?)
                ORDER BY detected_at ASC, face_id ASC
                """,
                (source_person_id, target_person_id),
            ).fetchall()
            source_faces = [
                self._face_from_row(row) for row in faces if row["person_id"] == source_person_id
            ]
            target_faces = [
                self._face_from_row(row) for row in faces if row["person_id"] == target_person_id
            ]
            manual_source_faces: list[ManualFaceRecord] = []
            manual_target_faces: list[ManualFaceRecord] = []
            profile_map = {}
            if self._state_repo is not None:
                manual_faces = self._state_repo.get_manual_faces_for_persons(
                    (source_person_id, target_person_id)
                )
                manual_source_faces = [
                    face for face in manual_faces if face.person_id == source_person_id
                ]
                manual_target_faces = [
                    face for face in manual_faces if face.person_id == target_person_id
                ]
                profile_map = {
                    profile.person_id: profile
                    for profile in self._state_repo.get_profiles()
                    if profile.person_id in {source_person_id, target_person_id}
                }
            if not (source_faces or manual_source_faces) or not (
                target_faces or manual_target_faces
            ):
                return False, {}

            person_rows = conn.execute(
                """
                SELECT person_id, name, created_at
                FROM persons WHERE person_id IN (?, ?)
                """,
                (source_person_id, target_person_id),
            ).fetchall()
            person_map = {row["person_id"]: row for row in person_rows}
            target_person = person_map.get(target_person_id)
            source_person = person_map.get(source_person_id)
            target_profile = profile_map.get(target_person_id)
            source_profile = profile_map.get(source_person_id)
            target_name = None
            target_created_at = _utc_now_iso()
            if target_person is not None:
                target_name = target_person["name"]
                target_created_at = target_person["created_at"]
            elif target_profile is not None:
                target_name = target_profile.name
                target_created_at = target_profile.created_at
            elif manual_target_faces:
                target_created_at = min(face.created_at for face in manual_target_faces)
            elif source_person is not None:
                target_name = source_person["name"]
                target_created_at = source_person["created_at"]
            elif source_profile is not None:
                target_name = source_profile.name
                target_created_at = source_profile.created_at
            elif manual_source_faces:
                target_created_at = min(face.created_at for face in manual_source_faces)

            conn.execute(
                "UPDATE faces SET person_id = ? WHERE person_id = ?",
                (target_person_id, source_person_id),
            )

            merged_faces = [
                FaceRecord(**{**face.__dict__, "person_id": target_person_id})
                for face in (target_faces + source_faces)
            ]
            center_embedding = np.empty((0,), dtype=np.float32)
            updated_at = _utc_now_iso()
            if merged_faces:
                key_face = max(merged_faces, key=_key_face_sort_key)
                center_embedding = compute_cluster_center(
                    np.stack([face.embedding for face in merged_faces], axis=0)
                )
                conn.execute(
                    """
                    INSERT INTO persons (
                        person_id, name, key_face_id, face_count, center_embedding,
                        created_at, updated_at, sample_count, profile_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        name = excluded.name,
                        key_face_id = excluded.key_face_id,
                        face_count = excluded.face_count,
                        center_embedding = excluded.center_embedding,
                        updated_at = excluded.updated_at,
                        sample_count = excluded.sample_count,
                        profile_state = excluded.profile_state
                    """,
                    (
                        target_person_id,
                        _normalize_name(target_name),
                        key_face.face_id,
                        len(merged_faces),
                        _serialize_embedding(center_embedding),
                        target_created_at,
                        updated_at,
                        len(merged_faces),
                        profile_state_for_sample_count(len(merged_faces)),
                    ),
                )
            else:
                conn.execute("DELETE FROM persons WHERE person_id = ?", (target_person_id,))
            conn.execute("DELETE FROM persons WHERE person_id = ?", (source_person_id,))
            conn.commit()

        if self._state_repo is not None:
            group_redirects = self._state_repo.merge_persons(
                source_person_id,
                target_person_id,
                center_embedding=center_embedding,
                target_name=target_name,
                target_created_at=target_created_at,
                sample_count=len(merged_faces),
                hidden_state=source_hidden,
            )
            self._sync_person_cover_defaults()
            self.refresh_all_group_assets()
        return True, group_redirects
    def _rebuild_runtime_person(self, person_id: str) -> bool:
        if not person_id:
            return False

        self.initialize()
        profile = None
        manual_faces: list[ManualFaceRecord] = []
        if self._state_repo is not None:
            manual_faces = self._state_repo.get_manual_faces_for_persons((person_id,))
            profile = self._state_repo.get_profile(person_id)

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE person_id = ?
                ORDER BY detected_at ASC, face_id ASC
                """,
                (person_id,),
            ).fetchall()
            auto_faces = [self._face_from_row(row) for row in rows]
            existing_person = conn.execute(
                """
                SELECT person_id, name, created_at
                FROM persons
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()

            if not auto_faces:
                conn.execute("DELETE FROM persons WHERE person_id = ?", (person_id,))
                conn.commit()
                return bool(manual_faces)

            name = existing_person["name"] if existing_person is not None else None
            if name is None and profile is not None:
                name = profile.name
            created_at = existing_person["created_at"] if existing_person is not None else None
            if created_at is None and profile is not None:
                created_at = profile.created_at
            if created_at is None and manual_faces:
                created_at = min(face.created_at for face in manual_faces)
            if created_at is None:
                created_at = min(face.detected_at for face in auto_faces)

            key_face = max(auto_faces, key=_key_face_sort_key)
            center_embedding = compute_cluster_center(
                np.stack([face.embedding for face in auto_faces], axis=0)
            )
            sample_count = len(auto_faces)
            updated_at = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO persons (
                    person_id, name, key_face_id, face_count, center_embedding,
                    created_at, updated_at, sample_count, profile_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    name = excluded.name,
                    key_face_id = excluded.key_face_id,
                    face_count = excluded.face_count,
                    center_embedding = excluded.center_embedding,
                    updated_at = excluded.updated_at,
                    sample_count = excluded.sample_count,
                    profile_state = excluded.profile_state
                """,
                (
                    person_id,
                    _normalize_name(name),
                    key_face.face_id,
                    sample_count,
                    _serialize_embedding(center_embedding),
                    created_at,
                    updated_at,
                    sample_count,
                    profile_state_for_sample_count(sample_count),
                ),
            )
            conn.commit()

        if self._state_repo is not None:
            self._state_repo.upsert_person_profile(
                person_id,
                name_or_none=name,
                created_at=created_at,
                center_embedding=center_embedding,
                sample_count=sample_count,
            )
        return True


__all__ = ["FaceMergeMixin"]
