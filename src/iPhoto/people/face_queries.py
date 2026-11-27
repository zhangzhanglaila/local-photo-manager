"""Read side of the face index: clusters, persons, groups and annotations.

Split out of ``FaceRepository`` (1398 lines, 45 methods). The connection, schema
and row mappers stay in the repository class; this mixin only reads. Bodies moved
verbatim, no SQL changed.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing

from .records import (
    AssetFaceAnnotation,
    FaceRecord,
    ManualFaceRecord,
    PeopleGroupRecord,
    PersonRecord,
    PersonSummary,
)
from .repository_utils import (
    _key_face_sort_key,
    _unique_person_ids,
    _utc_now_iso,
)


class FaceQueryMixin:
    """Queries over faces, persons, groups and their asset sets."""

    def get_all_faces(self) -> list[FaceRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                ORDER BY detected_at ASC, face_id ASC
                """).fetchall()
        rejected_face_keys: set[str] = set()
        if self._state_repo is not None:
            rejected_face_keys = self._state_repo.get_rejected_face_keys(
                row["face_key"] for row in rows if row["face_key"]
            )
        return [
            self._face_from_row(row)
            for row in rows
            if row["face_key"] not in rejected_face_keys
        ]
    def get_faces_by_asset_id(self, asset_id: str) -> list[FaceRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    face_id, face_key, asset_id, asset_rel, box_x, box_y, box_w, box_h,
                    confidence, embedding, embedding_dim, thumbnail_path, person_id,
                    detected_at, image_width, image_height
                FROM faces
                WHERE asset_id = ?
                ORDER BY detected_at ASC, face_id ASC
                """,
                (asset_id,),
            ).fetchall()
        rejected_face_keys: set[str] = set()
        if self._state_repo is not None:
            rejected_face_keys = self._state_repo.get_rejected_face_keys(
                row["face_key"] for row in rows if row["face_key"]
            )
        return [
            self._face_from_row(row)
            for row in rows
            if row["face_key"] not in rejected_face_keys
        ]
    def get_all_person_records(self) -> list[PersonRecord]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    person_id, name, key_face_id, face_count, center_embedding,
                    created_at, updated_at, sample_count, profile_state
                FROM persons
                ORDER BY created_at ASC, person_id ASC
                """
            ).fetchall()
        return [self._person_from_row(row) for row in rows]
    def count_persons(self) -> int:
        """Number of clustered people, without building their summaries."""

        if not self._db_path.exists():
            return 0
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM persons").fetchone()
        return int(row[0]) if row else 0
    def get_person_summaries(self, *, include_hidden: bool = False) -> list[PersonSummary]:
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute("""
                SELECT
                    persons.person_id,
                    persons.name,
                    persons.key_face_id,
                    persons.face_count,
                    persons.created_at,
                    faces.face_id,
                    faces.face_key,
                    faces.asset_id,
                    faces.thumbnail_path
                FROM persons
                LEFT JOIN faces ON faces.face_id = persons.key_face_id
                ORDER BY persons.face_count DESC, persons.created_at ASC
                """).fetchall()
            asset_count_rows = conn.execute("""
                SELECT person_id, COUNT(DISTINCT asset_id) AS asset_count
                FROM faces
                WHERE person_id IS NOT NULL
                GROUP BY person_id
                """).fetchall()
        asset_count_map: dict[str, int] = {
            str(row["person_id"]): int(row["asset_count"]) for row in asset_count_rows
        }
        auto_rows_by_person_id = {str(row["person_id"]): row for row in rows if row["person_id"]}
        manual_faces_by_person_id: dict[str, list[ManualFaceRecord]] = defaultdict(list)
        profile_map = {}
        order_map: dict[str, int] = {}
        hidden_map: dict[str, bool] = {}
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces():
                manual_faces_by_person_id[face.person_id].append(face)
            profile_map = {profile.person_id: profile for profile in self._state_repo.get_profiles()}
        person_ids = set(auto_rows_by_person_id) | set(manual_faces_by_person_id)
        cover_paths: dict[str, str] = {}
        if self._state_repo is not None and person_ids:
            cover_paths = self._state_repo.get_person_cover_thumbnail_map(
                person_ids
            )
            order_map = self._state_repo.get_person_order_map(person_ids)
            hidden_map = self._state_repo.get_person_hidden_map(person_ids)
        summaries: list[PersonSummary] = []
        for person_id in person_ids:
            row = auto_rows_by_person_id.get(person_id)
            manual_faces = manual_faces_by_person_id.get(person_id, [])
            profile = profile_map.get(person_id)
            auto_count = int(row["face_count"]) if row is not None else 0
            face_count = auto_count + len(manual_faces)
            if face_count <= 0:
                continue
            auto_asset_count = asset_count_map.get(person_id, 0)
            manual_asset_ids = {face.asset_id for face in manual_faces}
            asset_count = auto_asset_count + len(manual_asset_ids)
            key_face_id = (
                str(row["key_face_id"])
                if row is not None and row["key_face_id"]
                else manual_faces[0].face_id
            )
            name = row["name"] if row is not None else None
            if name is None and profile is not None:
                name = profile.name
            created_at = row["created_at"] if row is not None else None
            if created_at is None and profile is not None:
                created_at = profile.created_at
            if created_at is None:
                created_at = min((face.created_at for face in manual_faces), default=_utc_now_iso())
            thumbnail_path = cover_paths.get(person_id)
            if not thumbnail_path and row is not None:
                thumbnail_path = row["thumbnail_path"]
            if not thumbnail_path and manual_faces:
                thumbnail_path = manual_faces[0].thumbnail_path
            resolved_thumbnail: Path | None = None
            if thumbnail_path:
                resolved_thumbnail = (self._db_path.parent / thumbnail_path).resolve()
            summaries.append(
                PersonSummary(
                    person_id=person_id,
                    name=name,
                    key_face_id=key_face_id,
                    face_count=face_count,
                    thumbnail_path=resolved_thumbnail,
                    created_at=str(created_at),
                    is_hidden=bool(hidden_map.get(person_id, False)),
                    asset_count=asset_count,
                )
            )
        summaries.sort(key=lambda summary: (-summary.face_count, summary.created_at, summary.person_id))
        if order_map:
            fallback_order = {summary.person_id: index for index, summary in enumerate(summaries)}
            summaries.sort(
                key=lambda summary: (
                    order_map.get(summary.person_id, len(order_map) + fallback_order[summary.person_id]),
                    fallback_order[summary.person_id],
                )
            )
        if not include_hidden:
            summaries = [summary for summary in summaries if not summary.is_hidden]
        return summaries
    def is_person_hidden(self, person_id: str) -> bool:
        if self._state_repo is None:
            return False
        return self._state_repo.is_person_hidden(person_id)
    def get_asset_ids_by_person(self, person_id: str) -> list[str]:
        if not person_id:
            return []
        self.initialize()
        asset_dates: dict[str, str] = {}
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT asset_id, MAX(detected_at) AS last_detected_at
                FROM faces
                WHERE person_id = ?
                GROUP BY asset_id
                ORDER BY last_detected_at DESC, asset_id ASC
                """,
                (person_id,),
            ).fetchall()
        for row in rows:
            if row["asset_id"]:
                asset_dates[str(row["asset_id"])] = str(row["last_detected_at"])
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces_for_persons([person_id]):
                previous = asset_dates.get(face.asset_id)
                if previous is None or face.created_at > previous:
                    asset_dates[face.asset_id] = face.created_at
        ordered = sorted(asset_dates.items(), key=lambda item: item[0])
        ordered = sorted(ordered, key=lambda item: item[1], reverse=True)
        return [asset_id for asset_id, _last_seen in ordered]
    def get_person_ids_for_asset_ids(self, asset_ids: Iterable[str]) -> list[str]:
        ids = [str(asset_id) for asset_id in asset_ids if asset_id]
        if not ids:
            return []
        self.initialize()
        # Use a set to deduplicate person IDs across chunks before sorting.
        chunk_size = 900
        person_ids: set[str] = set()
        with closing(self._connect()) as conn:
            for start in range(0, len(ids), chunk_size):
                chunk = ids[start : start + chunk_size]
                placeholders = ", ".join(["?"] * len(chunk))
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT person_id
                    FROM faces
                    WHERE asset_id IN ({placeholders}) AND person_id IS NOT NULL
                    """,
                    chunk,
                ).fetchall()
                person_ids.update(str(row["person_id"]) for row in rows if row["person_id"])
        if self._state_repo is not None:
            person_ids.update(self._state_repo.get_manual_person_ids_for_asset_ids(ids))
        return sorted(person_ids)
    def list_asset_face_annotations(self, asset_id: str) -> list[AssetFaceAnnotation]:
        if not asset_id:
            return []
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    faces.face_id,
                    faces.face_key,
                    faces.person_id,
                    persons.name,
                    faces.box_x,
                    faces.box_y,
                    faces.box_w,
                    faces.box_h,
                    faces.image_width,
                    faces.image_height,
                    faces.thumbnail_path
                FROM faces
                LEFT JOIN persons ON persons.person_id = faces.person_id
                WHERE faces.asset_id = ?
                ORDER BY faces.box_x ASC, faces.box_y ASC, faces.face_id ASC
                """,
                (asset_id,),
            ).fetchall()
        rejected_face_keys: set[str] = set()
        if self._state_repo is not None:
            rejected_face_keys = self._state_repo.get_rejected_face_keys(
                row["face_key"] for row in rows if row["face_key"]
            )
        annotations = [
            AssetFaceAnnotation(
                face_id=str(row["face_id"]),
                person_id=str(row["person_id"]) if row["person_id"] else None,
                display_name=row["name"],
                box_x=int(row["box_x"]),
                box_y=int(row["box_y"]),
                box_w=int(row["box_w"]),
                box_h=int(row["box_h"]),
                image_width=int(row["image_width"]),
                image_height=int(row["image_height"]),
                thumbnail_path=(
                    (self._db_path.parent / str(row["thumbnail_path"])).resolve()
                    if row["thumbnail_path"]
                    else None
                ),
                is_manual=False,
            )
            for row in rows
            if row["face_id"] and row["face_key"] not in rejected_face_keys
        ]
        if self._state_repo is not None:
            manual_faces = self._state_repo.get_manual_faces_for_asset(asset_id)
            names = self._state_repo.get_profile_name_map(
                face.person_id for face in manual_faces
            )
            missing_name_ids = [
                face.person_id
                for face in manual_faces
                if face.person_id not in names or names[face.person_id] is None
            ]
            if missing_name_ids:
                placeholders = ", ".join(["?"] * len(set(missing_name_ids)))
                with closing(self._connect()) as conn:
                    name_rows = conn.execute(
                        f"""
                        SELECT person_id, name
                        FROM persons
                        WHERE person_id IN ({placeholders})
                        """,
                        list(dict.fromkeys(missing_name_ids)),
                    ).fetchall()
                names.update(
                    {
                        str(row["person_id"]): row["name"]
                        for row in name_rows
                        if row["person_id"] and row["name"] is not None
                    }
                )
            annotations.extend(
                AssetFaceAnnotation(
                    face_id=face.face_id,
                    person_id=face.person_id,
                    display_name=names.get(face.person_id),
                    box_x=face.box_x,
                    box_y=face.box_y,
                    box_w=face.box_w,
                    box_h=face.box_h,
                    image_width=face.image_width,
                    image_height=face.image_height,
                    thumbnail_path=(
                        (self._db_path.parent / face.thumbnail_path).resolve()
                        if face.thumbnail_path
                        else None
                    ),
                    is_manual=True,
                )
                for face in manual_faces
            )
        annotations.sort(key=lambda face: (face.box_x, face.box_y, face.face_id))
        return annotations
    def get_common_asset_ids_for_persons(self, member_person_ids: Iterable[str]) -> list[str]:
        return [
            asset_id
            for asset_id, _last_detected_at in self._common_asset_rows_for_persons(
                member_person_ids
            )
        ]
    def _common_asset_rows_for_persons(
        self,
        member_person_ids: Iterable[str],
    ) -> list[tuple[str, str]]:
        members = _unique_person_ids(member_person_ids)
        if len(members) < 2:
            return []

        self.initialize()
        hits_by_asset_id: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
        placeholders = ", ".join(["?"] * len(members))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    person_id,
                    asset_id,
                    MAX(detected_at) AS last_detected_at,
                    MAX(rowid) AS last_face_rowid
                FROM faces
                WHERE person_id IN ({placeholders})
                GROUP BY person_id, asset_id
                """,
                members,
            ).fetchall()
        for row in rows:
            if row["person_id"] and row["asset_id"]:
                hits_by_asset_id[str(row["asset_id"])][str(row["person_id"])] = (
                    str(row["last_detected_at"]),
                    int(row["last_face_rowid"] or 0),
                )
        if self._state_repo is not None:
            for face in self._state_repo.get_manual_faces_for_persons(members):
                person_hits = hits_by_asset_id[face.asset_id]
                previous = person_hits.get(face.person_id)
                manual_hit = (face.created_at, 0)
                if previous is None or manual_hit > previous:
                    person_hits[face.person_id] = manual_hit

        member_set = set(members)
        common_rows = [
            (asset_id, *max(person_hits.values()))
            for asset_id, person_hits in hits_by_asset_id.items()
            if member_set.issubset(person_hits)
        ]
        common_rows = sorted(common_rows, key=lambda item: item[0])
        common_rows = sorted(common_rows, key=lambda item: (item[1], item[2]), reverse=True)
        return [(asset_id, last_detected_at) for asset_id, last_detected_at, _rowid in common_rows]
    def get_common_asset_ids_for_group(self, group_id: str) -> list[str]:
        if self._state_repo is None:
            return []
        self.initialize()
        if self._state_repo.has_group_asset_cache(group_id):
            return self._state_repo.get_group_asset_ids(group_id)
        return self.refresh_group_assets(group_id)
    def get_group_cover_asset_id(self, group_id: str) -> str | None:
        if self._state_repo is None:
            return None
        return self._state_repo.get_group_cover_asset_id(group_id)
    def list_groups(self) -> list[PeopleGroupRecord]:
        if self._state_repo is None:
            return []
        self.initialize()
        return self._state_repo.list_groups()
    def get_group(self, group_id: str) -> PeopleGroupRecord | None:
        if self._state_repo is None:
            return None
        self.initialize()
        return self._state_repo.get_group(group_id)


__all__ = ["FaceQueryMixin"]
