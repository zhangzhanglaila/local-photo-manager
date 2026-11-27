"""Distance and clustering maths for the face pipeline.

Pure functions, no Qt and no database: cosine distance, DBSCAN over a distance
matrix, bbox normalisation and the quantisation helpers used to build stable face
keys. Split out of ``people/pipeline.py``; bodies moved verbatim.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .records import FaceRecord
from .repository_utils import normalize_vector


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


__all__ = [
    "cosine_distance",
    "cosine_distance_matrix",
    "run_dbscan",
]
