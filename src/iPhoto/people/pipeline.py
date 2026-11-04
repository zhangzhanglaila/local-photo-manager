"""Face detection and clustering helpers for the People feature."""

from __future__ import annotations

import logging
import os
import sys
import builtins
import typing
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import ModuleType
from typing import Callable, Sequence

import numpy as np

from .image_utils import load_image_rgb, pil_image_to_bgr, save_face_thumbnail
from .repository import (
    FaceRecord,
    FaceStateRepository,
    PersonProfile,
    PersonRecord,
    compute_cluster_center,
    normalize_vector,
)
from .repository_utils import profile_state_for_sample_count

_LOGGER = logging.getLogger(__name__)
_REQUIRED_FACE_MODULES = ("detection", "recognition")


def _make_progress_download(on_progress: "Callable[[int, int], None]"):
    """Return a drop-in replacement for ``insightface.utils.download.download_file``
    that reports download progress via *on_progress(downloaded_bytes, total_bytes)*
    instead of printing a tqdm bar to the console."""

    import requests as _requests

    def _download_file_with_progress(url, path=None, overwrite=False, sha1_hash=None):
        from insightface.utils.download import check_sha1

        if path is None:
            fname = url.split("/")[-1]
        else:
            path = os.path.expanduser(path)
            if os.path.isdir(path):
                fname = os.path.join(path, url.split("/")[-1])
            else:
                fname = path

        if overwrite or not os.path.exists(fname) or (
            sha1_hash and not check_sha1(fname, sha1_hash)
        ):
            dirname = os.path.dirname(os.path.abspath(os.path.expanduser(fname)))
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            _LOGGER.info("Downloading model from %s ...", url)
            r = _requests.get(url, stream=True)
            if r.status_code != 200:
                raise RuntimeError("Failed downloading url %s" % url)
            total_length = r.headers.get("content-length")
            total = int(total_length) if total_length is not None else 0
            downloaded = 0
            on_progress(0, total)
            with open(fname, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        on_progress(downloaded, total)

            if sha1_hash and not check_sha1(fname, sha1_hash):
                raise UserWarning(
                    f"File {fname} is downloaded but the content hash does not match."
                )

        return fname

    return _download_file_with_progress


@dataclass(frozen=True)
class DetectedAssetFaces:
    asset_id: str
    asset_rel: str
    faces: list[FaceRecord]
    error: str | None = None


class FaceClusterPipeline:
    def __init__(
        self,
        *,
        model_root: Path,
        model_pack: str = "buffalo_l",
        distance_threshold: float = 0.5,
        min_samples: int = 2,
        min_face_size: int = 60,
        min_confidence: float = 0.6,
        on_download_progress: "Callable[[int, int], None] | None" = None,
    ) -> None:
        self._model_root = Path(model_root)
        self._model_pack = model_pack
        self._distance_threshold = float(distance_threshold)
        self._min_samples = int(min_samples)
        self._min_face_size = int(min_face_size)
        self._min_confidence = float(min_confidence)
        self._analysis_app = None
        self._on_download_progress = on_download_progress

    @property
    def distance_threshold(self) -> float:
        return self._distance_threshold

    @property
    def min_samples(self) -> int:
        return self._min_samples

    def detect_faces_for_rows(
        self,
        rows: list[dict],
        *,
        library_root: Path,
        thumbnail_dir: Path,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[DetectedAssetFaces]:
        if not rows:
            return []

        face_app = self._ensure_face_analysis()
        cancellation_requested = is_cancelled or (lambda: False)
        results: list[DetectedAssetFaces] = []
        for row in rows:
            if cancellation_requested():
                break
            asset_id = str(row.get("id") or "")
            asset_rel = Path(str(row.get("rel") or "")).as_posix()
            image_path = (library_root / asset_rel).resolve()
            try:
                image = load_image_rgb(image_path)
                image_bgr = pil_image_to_bgr(image)
                detected_faces = face_app.get(image_bgr)
            except Exception as exc:
                if cancellation_requested():
                    break
                _LOGGER.warning("Face detection failed for %s: %s", image_path, exc)
                results.append(
                    DetectedAssetFaces(
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        faces=[],
                        error=str(exc),
                    )
                )
                continue

            if cancellation_requested():
                break

            image_width, image_height = image.size
            image_area = image_width * image_height

            # Pre-filter: skip faces that are too small relative to the image.
            # This removes poster/screen faces in the background.
            MIN_FACE_AREA_RATIO = 0.005  # 0.5% of image area
            candidates = []
            for detected in detected_faces:
                det_score = float(getattr(detected, "det_score", 0.0))
                if det_score < self._min_confidence:
                    continue
                bbox = _normalize_bbox(
                    detected.bbox,
                    image_width=image_width,
                    image_height=image_height,
                )
                if bbox[2] < self._min_face_size or bbox[3] < self._min_face_size:
                    continue
                face_area = bbox[2] * bbox[3]
                if image_area > 0 and face_area / image_area < MIN_FACE_AREA_RATIO:
                    continue
                candidates.append((detected, bbox, det_score))

            # Relative filter: remove faces much smaller than the largest face
            # in the same image (poster/painting faces in the background).
            if len(candidates) > 1:
                max_area = max(b[2] * b[3] for _, b, _ in candidates)
                candidates = [
                    (d, b, s) for d, b, s in candidates
                    if (b[2] * b[3]) >= max_area * 0.15
                ]

            faces: list[FaceRecord] = []
            for detected, bbox, det_score in candidates:
                embedding = _extract_embedding(detected)
                if embedding is None:
                    continue

                face_id = uuid.uuid4().hex
                thumbnail_path = thumbnail_dir / f"{face_id}.jpg"
                save_face_thumbnail(image, bbox, thumbnail_path)
                faces.append(
                    FaceRecord(
                        face_id=face_id,
                        face_key=build_face_key(
                            asset_id=asset_id,
                            bbox=bbox,
                            image_width=image_width,
                            image_height=image_height,
                        ),
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        box_x=bbox[0],
                        box_y=bbox[1],
                        box_w=bbox[2],
                        box_h=bbox[3],
                        confidence=det_score,
                        embedding=embedding,
                        embedding_dim=int(embedding.shape[0]),
                        thumbnail_path=thumbnail_path.relative_to(thumbnail_dir.parent).as_posix(),
                        person_id=None,
                        detected_at=_utc_now_iso(),
                        image_width=image_width,
                        image_height=image_height,
                    )
                )

            results.append(
                DetectedAssetFaces(
                    asset_id=asset_id,
                    asset_rel=asset_rel,
                    faces=faces,
                )
            )
        return results

    def _ensure_face_analysis(self):
        if self._analysis_app is not None:
            return self._analysis_app

        try:
            _install_runtime_typing_compat()
            _install_insightface_mask_renderer_stubs()
            from insightface.app.face_analysis import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError(
                "Face scanning unavailable: install the optional AI dependencies and rescan."
            ) from exc

        self._model_root.mkdir(parents=True, exist_ok=True)
        insightface_root = self._model_root.parent.resolve()
        # Keep downloaded models in the shared extension cache instead of
        # library-specific folders so they are reused across rescans.
        os.environ["INSIGHTFACE_HOME"] = str(insightface_root)
        _patch_insightface_alignment_estimate()
        providers = _resolve_execution_providers()
        ctx_id = 0 if "CUDAExecutionProvider" in providers else -1

        # Monkey-patch InsightFace download to report progress to the UI.
        original_download_file = None
        if self._on_download_progress is not None:
            try:
                import insightface.utils.download as _dl_mod
                original_download_file = _dl_mod.download_file
                _dl_mod.download_file = _make_progress_download(self._on_download_progress)
                # Also patch the reference in storage module
                import insightface.utils.storage as _stor_mod
                _stor_mod.download_file = _dl_mod.download_file
            except Exception:
                original_download_file = None

        try:
            app = FaceAnalysis(
                name=self._model_pack,
                root=str(insightface_root),
                allowed_modules=list(_REQUIRED_FACE_MODULES),
                providers=providers,
                sess_options=_face_session_options(),
            )
            app.prepare(ctx_id=ctx_id, det_size=(640, 640))
        except Exception as exc:
            raise _build_face_analysis_init_error(
                feature_name="Face scanning",
                model_pack=self._model_pack,
                model_dir=self._model_root.resolve(),
                exc=exc,
            ) from exc
        finally:
            # Restore original download function
            if original_download_file is not None:
                try:
                    import insightface.utils.download as _dl_mod
                    import insightface.utils.storage as _stor_mod
                    _dl_mod.download_file = original_download_file
                    _stor_mod.download_file = original_download_file
                except Exception:
                    pass

        self._analysis_app = app
        return app


def build_face_key(
    *,
    asset_id: str,
    bbox: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    quantization: int = 8,
) -> str:
    x, y, width, height = bbox
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    quantized = (
        _quantize_value(center_x, quantization),
        _quantize_value(center_y, quantization),
        _quantize_value(width, quantization),
        _quantize_value(height, quantization),
    )
    payload = (
        f"{asset_id}|{image_width}x{image_height}|"
        f"{quantized[0]}|{quantized[1]}|{quantized[2]}|{quantized[3]}"
    )
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def _merge_close_clusters(
    persons: list[PersonRecord],
    faces: list[FaceRecord],
    *,
    merge_threshold: float = 0.35,
) -> tuple[list[PersonRecord], list[FaceRecord]]:
    """Merge clusters whose center embeddings are very close.

    DBSCAN can split a single person into multiple clusters when face
    angles, lighting, or colour grading create gaps in embedding space.
    This post-pass reunites clusters whose centres fall within
    *merge_threshold* (cosine distance).
    """
    if len(persons) <= 1:
        return persons, faces

    # Build face lists per person.
    faces_by_pid: dict[str, list[FaceRecord]] = defaultdict(list)
    for face in faces:
        if face.person_id is not None:
            faces_by_pid[face.person_id].append(face)

    # Union-Find for merging.
    parent: dict[str, str] = {p.person_id: p.person_id for p in persons}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Compare all pairs of cluster centres.
    for i, a in enumerate(persons):
        for b in persons[i + 1:]:
            if a.center_embedding.size == 0 or b.center_embedding.size == 0:
                continue
            if a.center_embedding.shape != b.center_embedding.shape:
                continue
            dist = cosine_distance(a.center_embedding, b.center_embedding)
            if dist <= merge_threshold:
                union(a.person_id, b.person_id)

    # Check if any merges happened.
    groups: dict[str, list[str]] = defaultdict(list)
    for pid in parent:
        groups[find(pid)].append(pid)
    merged_groups = {root: members for root, members in groups.items() if len(members) > 1}
    if not merged_groups:
        return persons, faces

    # Build merged person records and update face assignments.
    old_persons = {p.person_id: p for p in persons}
    new_persons: list[PersonRecord] = []
    removed_pids: set[str] = set()
    face_updates: dict[str, str] = {}  # old_pid -> new_pid

    for root, members in merged_groups.items():
        all_member_faces: list[FaceRecord] = []
        for pid in members:
            all_member_faces.extend(faces_by_pid.get(pid, []))
            removed_pids.add(pid)

        if not all_member_faces:
            continue

        # Use the person with the most faces as the base.
        base_pid = max(members, key=lambda p: len(faces_by_pid.get(p, [])))
        new_center = compute_cluster_center(
            np.stack([f.embedding for f in all_member_faces], axis=0)
        )
        key_face = max(all_member_faces, key=_key_face_sort_key)
        base_person = old_persons[base_pid]

        merged_person = replace(
            base_person,
            key_face_id=key_face.face_id,
            face_count=len(all_member_faces),
            center_embedding=new_center,
            sample_count=len(all_member_faces),
            profile_state=profile_state_for_sample_count(len(all_member_faces)),
        )
        new_persons.append(merged_person)

        for pid in members:
            if pid != base_pid:
                face_updates[pid] = base_pid

    # Keep unmerged persons.
    for p in persons:
        if p.person_id not in removed_pids:
            new_persons.append(p)

    # Update face person_ids.
    updated_faces = [
        replace(face, person_id=face_updates[face.person_id])
        if face.person_id in face_updates
        else face
        for face in faces
    ]

    return new_persons, updated_faces


def cluster_face_records(
    faces: list[FaceRecord],
    *,
    distance_threshold: float = 0.6,
    min_samples: int = 2,
) -> tuple[list[FaceRecord], list[PersonRecord]]:
    if not faces:
        return [], []

    # Limit faces to avoid OOM from N×N distance matrix (5000×5000×4 ≈ 100MB).
    MAX_CLUSTER_FACES = 5000
    if len(faces) > MAX_CLUSTER_FACES:
        # Keep existing clustered faces + most recent unclustered ones.
        clustered = [f for f in faces if f.person_id]
        unclustered = [f for f in faces if not f.person_id]
        if len(clustered) >= MAX_CLUSTER_FACES:
            faces = clustered[-MAX_CLUSTER_FACES:]
        else:
            remaining = MAX_CLUSTER_FACES - len(clustered)
            faces = clustered + unclustered[-remaining:]

    embeddings = np.stack([face.embedding for face in faces], axis=0).astype(np.float32)
    labels = run_dbscan(
        embeddings,
        eps=distance_threshold,
        min_samples=min_samples,
    )

    grouped_indices: dict[str, list[int]] = defaultdict(list)
    noise_indices: list[int] = []
    for index, label in enumerate(labels.tolist()):
        if label == -1:
            noise_indices.append(index)
        else:
            grouped_indices[f"cluster-{label}"].append(index)

    updated_faces = list(faces)
    persons: list[PersonRecord] = []
    for indices in grouped_indices.values():
        members = [faces[index] for index in indices]
        key_face = max(members, key=_key_face_sort_key)
        person_id = uuid.uuid4().hex
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        timestamp = _utc_now_iso()
        persons.append(
            PersonRecord(
                person_id=person_id,
                name=None,
                key_face_id=key_face.face_id,
                face_count=len(members),
                center_embedding=center_embedding,
                created_at=timestamp,
                updated_at=timestamp,
                sample_count=len(members),
                profile_state=profile_state_for_sample_count(len(members)),
            )
        )
        for index in indices:
            updated_faces[index] = replace(updated_faces[index], person_id=person_id)

    # Noise faces (label -1) are left without a person_id — they won't
    # appear as individual "person" entries in the People dashboard.

    # Post-DBSCAN merge: combine clusters whose center embeddings are very
    # close.  DBSCAN can split a single person into multiple clusters when
    # face angles or lighting create gaps in embedding space.  This merge
    # pass reunites them.
    persons, updated_faces = _merge_close_clusters(
        persons, updated_faces, merge_threshold=0.35,
    )

    persons.sort(key=lambda person: (-person.face_count, person.created_at))
    return updated_faces, persons


def build_person_records_from_faces(
    faces: Sequence[FaceRecord],
    *,
    names_by_person_id: dict[str, str | None] | None = None,
    created_at_by_person_id: dict[str, str] | None = None,
) -> list[PersonRecord]:
    if not faces:
        return []

    grouped: dict[str, list[FaceRecord]] = defaultdict(list)
    for face in faces:
        if face.person_id:
            grouped[str(face.person_id)].append(face)

    if not grouped:
        return []

    resolved_names = dict(names_by_person_id or {})
    resolved_created_at = dict(created_at_by_person_id or {})
    updated_at = _utc_now_iso()
    persons: list[PersonRecord] = []
    for person_id, members in grouped.items():
        key_face = max(members, key=_key_face_sort_key)
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        sample_count = len(members)
        persons.append(
            PersonRecord(
                person_id=person_id,
                name=resolved_names.get(person_id),
                key_face_id=key_face.face_id,
                face_count=sample_count,
                center_embedding=center_embedding,
                created_at=resolved_created_at.get(
                    person_id,
                    min((member.detected_at for member in members), default=updated_at),
                ),
                updated_at=updated_at,
                sample_count=sample_count,
                profile_state=profile_state_for_sample_count(sample_count),
            )
        )
    persons.sort(key=lambda person: (-person.face_count, person.created_at))
    return persons


def canonicalize_cluster_identities(
    faces: list[FaceRecord],
    persons: list[PersonRecord],
    state_repository: FaceStateRepository,
    *,
    distance_threshold: float,
) -> tuple[list[FaceRecord], list[PersonRecord]]:
    if not faces or not persons:
        return faces, persons

    profiles = {profile.person_id: profile for profile in state_repository.get_profiles()}
    face_key_map = state_repository.get_face_key_map(face.face_key for face in faces)

    faces_by_person_id: dict[str, list[FaceRecord]] = defaultdict(list)
    for face in faces:
        if face.person_id is not None:
            faces_by_person_id[face.person_id].append(face)

    canonical_members: dict[str, list[FaceRecord]] = defaultdict(list)
    canonical_names: dict[str, str | None] = {}
    canonical_created_at: dict[str, str] = {}

    for person in persons:
        members = faces_by_person_id.get(person.person_id, [])
        canonical_id = resolve_canonical_person_id(
            person,
            members,
            profiles=profiles,
            face_key_map=face_key_map,
            distance_threshold=distance_threshold,
        )
        profile = profiles.get(canonical_id)
        canonical_members[canonical_id].extend(members)
        canonical_names.setdefault(canonical_id, profile.name if profile is not None else None)
        canonical_created_at.setdefault(
            canonical_id,
            profile.created_at if profile is not None else person.created_at,
        )

    updated_faces = list(faces)
    faces_by_face_id = {face.face_id: index for index, face in enumerate(faces)}
    for canonical_id, members in canonical_members.items():
        if not members:
            continue
        for member in members:
            updated_faces[faces_by_face_id[member.face_id]] = replace(member, person_id=canonical_id)
    canonical_persons = build_person_records_from_faces(
        updated_faces,
        names_by_person_id=canonical_names,
        created_at_by_person_id=canonical_created_at,
    )
    return updated_faces, canonical_persons


def resolve_canonical_person_id(
    person: PersonRecord,
    members: list[FaceRecord],
    *,
    profiles: dict[str, PersonProfile],
    face_key_map: dict[str, str],
    distance_threshold: float,
) -> str:
    vote_counter = Counter(
        face_key_map[member.face_key]
        for member in members
        if member.face_key in face_key_map
    )
    if vote_counter:
        return max(
            vote_counter.items(),
            key=lambda item: (
                item[1],
                profiles[item[0]].updated_at if item[0] in profiles else "",
                item[0],
            ),
        )[0]

    best_profile_id: str | None = None
    best_distance = float("inf")
    for profile in profiles.values():
        if str(profile.profile_state or "unstable") != "stable":
            continue
        if profile.embedding_dim <= 0 or profile.center_embedding.size == 0:
            continue
        if profile.center_embedding.shape != person.center_embedding.shape:
            continue
        distance = cosine_distance(person.center_embedding, profile.center_embedding)
        if distance < best_distance:
            best_distance = distance
            best_profile_id = profile.person_id

    if best_profile_id is not None and best_distance <= distance_threshold:
        return best_profile_id

    return uuid.uuid4().hex


def run_dbscan(
    embeddings: np.ndarray,
    *,
    eps: float,
    min_samples: int,
) -> np.ndarray:
    if embeddings.size == 0:
        return np.empty((0,), dtype=np.int32)

    n = embeddings.shape[0]
    normalized = np.stack([normalize_vector(v) for v in embeddings], axis=0)
    CHUNK_SIZE = 512

    # Build neighbor lists in chunks to avoid materializing the full N×N
    # distance matrix.  For large face sets (5000+) this keeps peak memory
    # at ~CHUNK_SIZE×N instead of N×N.
    neighbor_map: list[list[int]] = [[] for _ in range(n)]
    for start in range(0, n, CHUNK_SIZE):
        end = min(start + CHUNK_SIZE, n)
        # (chunk, n) @ (n, n) → (chunk, n)
        similarity_chunk = normalized[start:end] @ normalized.T
        distance_chunk = np.clip(1.0 - similarity_chunk, 0.0, 2.0)
        for local_idx in range(distance_chunk.shape[0]):
            global_idx = start + local_idx
            neighbor_map[global_idx] = [
                int(j) for j in np.flatnonzero(distance_chunk[local_idx] <= eps)
            ]
        del similarity_chunk, distance_chunk

    unvisited = -99
    labels = np.full(n, unvisited, dtype=np.int32)
    cluster_id = 0
    for point_index in range(n):
        if labels[point_index] != unvisited:
            continue

        neighbors = neighbor_map[point_index]
        if len(neighbors) < min_samples:
            labels[point_index] = -1
            continue

        labels[point_index] = cluster_id
        queue: deque[int] = deque(neighbors)
        queued = set(neighbors)
        while queue:
            neighbor_index = queue.popleft()
            queued.discard(neighbor_index)
            if labels[neighbor_index] == -1:
                labels[neighbor_index] = cluster_id
            if labels[neighbor_index] != unvisited:
                continue

            labels[neighbor_index] = cluster_id
            neighbor_neighbors = neighbor_map[neighbor_index]
            if len(neighbor_neighbors) < min_samples:
                continue
            for candidate in neighbor_neighbors:
                if labels[candidate] == unvisited and candidate not in queued:
                    queue.append(candidate)
                    queued.add(candidate)
                elif labels[candidate] == -1:
                    labels[candidate] = cluster_id

        cluster_id += 1

    labels[labels == unvisited] = -1
    return labels


def cosine_distance_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Compute full N×N cosine distance matrix (kept for callers that need it).

    Prefer using :func:`run_dbscan` directly for clustering — it computes
    distances in chunks without materializing the full matrix.
    """
    n = embeddings.shape[0]
    normalized = np.stack([normalize_vector(vector) for vector in embeddings], axis=0)
    CHUNK_SIZE = 512
    distance = np.empty((n, n), dtype=np.float32)
    for start in range(0, n, CHUNK_SIZE):
        end = min(start + CHUNK_SIZE, n)
        similarity_chunk = normalized[start:end] @ normalized.T
        distance[start:end] = 1.0 - similarity_chunk
    np.clip(distance, 0.0, 2.0, out=distance)
    return distance


def cosine_distance(left: np.ndarray, right: np.ndarray) -> float:
    left_normalized = normalize_vector(left)
    right_normalized = normalize_vector(right)
    if left_normalized.size == 0 or right_normalized.size == 0:
        return float("inf")
    similarity = float(left_normalized @ right_normalized)
    return float(np.clip(1.0 - similarity, 0.0, 2.0))


def _normalize_bbox(
    raw_bbox,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    box = np.asarray(raw_bbox, dtype=np.float32).flatten().tolist()
    x1, y1, x2, y2 = [int(round(value)) for value in box[:4]]
    x1 = max(0, min(x1, image_width - 1))
    y1 = max(0, min(y1, image_height - 1))
    x2 = max(x1 + 1, min(x2, image_width))
    y2 = max(y1 + 1, min(y2, image_height))
    return x1, y1, x2 - x1, y2 - y1


def _extract_embedding(face) -> np.ndarray | None:
    embedding = getattr(face, "embedding", None)
    if embedding is None:
        return None
    return normalize_vector(np.asarray(embedding, dtype=np.float32).flatten())


def _key_face_sort_key(face: FaceRecord) -> tuple[float, int]:
    return face.confidence, face.box_w * face.box_h


def _quantize_value(value: float, step: int) -> int:
    return int(round(float(value) / float(step)) * step)


def _build_face_analysis_init_error(
    *,
    feature_name: str,
    model_pack: str,
    model_dir: Path,
    exc: Exception,
) -> RuntimeError:
    reason = str(exc).strip() or exc.__class__.__name__
    model_pack_dir = model_dir / model_pack
    if not model_pack_dir.exists():
        return RuntimeError(
            f"{feature_name} unavailable: InsightFace model '{model_pack}' is not cached at "
            f"'{model_pack_dir}'. Initialization/download failed ({reason}). "
            f"Allow one download from github.com or copy an existing '{model_pack}' model "
            f"folder into '{model_dir}', then retry."
        )
    return RuntimeError(
        f"{feature_name} unavailable: failed to initialize InsightFace model "
        f"'{model_pack}' from '{model_pack_dir}' ({reason})."
    )


FACE_THREAD_LIMIT = 4


def _face_session_options():
    """Return bounded onnxruntime session options for face inference.

    InsightFace builds its sessions through ``onnxruntime.InferenceSession``
    without options, which defaults to one intra-op thread per core. A single
    face scan therefore pinned every core of the machine, and the interface
    became unresponsive for as long as the scan ran — the face scan starts on its
    own once the file scan finishes, so this happened without the user asking for
    it. The CLIP embedder already limits itself the same way.

    ``IPHOTO_FACE_THREADS`` overrides the limit for users who would rather finish
    a scan quickly.
    """

    import onnxruntime as ort

    options = ort.SessionOptions()
    threads = max(1, min(FACE_THREAD_LIMIT, (os.cpu_count() or FACE_THREAD_LIMIT) // 2))
    override = os.environ.get("IPHOTO_FACE_THREADS", "").strip()
    if override.isdigit() and int(override) > 0:
        threads = int(override)
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    return options


def _resolve_execution_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return ["CPUExecutionProvider"]

    available = ort.get_available_providers()
    providers: list[str] = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in available:
        providers.append("CPUExecutionProvider")
    return providers or ["CPUExecutionProvider"]


def _patch_insightface_alignment_estimate() -> None:
    try:
        from insightface.utils import face_align
        from skimage import transform as trans
    except ImportError:
        return

    if getattr(face_align, "_iphoto_from_estimate_patch", False):
        return

    similarity_transform_cls = getattr(trans, "SimilarityTransform", None)
    from_estimate = getattr(similarity_transform_cls, "from_estimate", None)
    if from_estimate is None:
        return

    def estimate_norm(lmk, image_size=112, mode="arcface"):
        del mode
        assert lmk.shape == (5, 2)
        assert image_size % 112 == 0 or image_size % 128 == 0
        if image_size % 112 == 0:
            ratio = float(image_size) / 112.0
            diff_x = 0.0
        else:
            ratio = float(image_size) / 128.0
            diff_x = 8.0 * ratio

        dst = face_align.arcface_dst * ratio
        dst[:, 0] += diff_x
        tform = similarity_transform_cls.from_estimate(lmk, dst)
        return tform.params[0:2, :]

    face_align.estimate_norm = estimate_norm
    face_align._iphoto_from_estimate_patch = True


def _install_insightface_mask_renderer_stubs() -> None:
    """Avoid importing albumentations for InsightFace mask rendering we do not use."""
    if "albumentations" in sys.modules:
        return

    albumentations_module = ModuleType("albumentations")
    core_module = ModuleType("albumentations.core")
    transforms_module = ModuleType("albumentations.core.transforms_interface")

    class ImageOnlyTransform:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

    transforms_module.ImageOnlyTransform = ImageOnlyTransform
    core_module.transforms_interface = transforms_module
    albumentations_module.core = core_module

    sys.modules["albumentations"] = albumentations_module
    sys.modules["albumentations.core"] = core_module
    sys.modules["albumentations.core.transforms_interface"] = transforms_module


def _install_runtime_typing_compat() -> None:
    """Provide typing names some third-party annotations expect at runtime."""
    import numpy.typing as npt

    compat_names = {
        "Any": typing.Any,
        "Callable": typing.Callable,
        "ClassVar": typing.ClassVar,
        "Concatenate": typing.Concatenate,
        "Dict": typing.Dict,
        "Final": typing.Final,
        "Generic": typing.Generic,
        "Iterable": typing.Iterable,
        "List": typing.List,
        "Literal": typing.Literal,
        "LiteralString": typing.LiteralString,
        "Mapping": typing.Mapping,
        "MutableMapping": typing.MutableMapping,
        "Never": typing.Never,
        "NoReturn": typing.NoReturn,
        "NotRequired": typing.NotRequired,
        "Optional": typing.Optional,
        "ParamSpec": typing.ParamSpec,
        "Protocol": typing.Protocol,
        "Required": typing.Required,
        "Self": typing.Self,
        "Sequence": typing.Sequence,
        "Set": typing.Set,
        "TypedDict": typing.TypedDict,
        "Tuple": typing.Tuple,
        "TypeAlias": typing.TypeAlias,
        "TypeGuard": typing.TypeGuard,
        "TypeVar": typing.TypeVar,
        "Union": typing.Union,
        "ArrayLike": npt.ArrayLike,
        "DTypeLike": npt.DTypeLike,
        "NDArray": npt.NDArray,
        "ndarray": np.ndarray,
    }
    for name, value in compat_names.items():
        if not hasattr(builtins, name):
            setattr(builtins, name, value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
