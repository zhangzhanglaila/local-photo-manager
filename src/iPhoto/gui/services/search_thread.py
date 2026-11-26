"""Background semantic-search worker and its row/embedding helpers.

The worker used to be a class nested inside ``MainCoordinator._handle_search_requested``.
Inside a nested class ``self`` is the worker, so the two ``self._active_search_spec()``
calls that meant "the coordinator's model" raised ``AttributeError``; the broad
``except`` turned that into a misleading "AI model failed to load" and search never
produced results. Everything the worker needs is now an explicit constructor
argument, and the model spec it ranks with is the spec it was created with.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from iPhoto.application.search.embedding_cache import EmbeddingCache
from iPhoto.config import SearchSettings

_LOGGER = logging.getLogger(__name__)

#: Cache of stored embeddings per library, keyed by the DB mtime. Reading ~10k rows
#: takes a few hundred milliseconds and used to happen on every single search.
_SEARCH_EMBEDDINGS_CACHE = EmbeddingCache()

#: Reverse-geocoded place labels per library; rebuilt when the index changes.
_LOCATION_INDEX_CACHE: dict[str, list[tuple[str, str]]] = {}


def load_search_embeddings(library_root: Path, model_name: str | None = None) -> list:
    """Return embeddings for *library_root*, reloading only when the DB changed.

    *model_name* keeps the cache key distinct per model: switching models must not
    serve the previous model's vectors, which live in a different space.
    """

    try:
        from iPhoto.utils.pathutils import resolve_work_dir

        work_dir = resolve_work_dir(library_root)
        db_path = work_dir / "embeddings.db" if work_dir is not None else None
        mtime = db_path.stat().st_mtime if db_path is not None and db_path.exists() else 0.0
    except Exception:
        mtime = 0.0

    from iPhoto.bootstrap.embedding_index import load_stored_embeddings

    return _SEARCH_EMBEDDINGS_CACHE.get(
        f"{library_root}|{model_name or '*'}",
        mtime,
        lambda: load_stored_embeddings(library_root, model_name=model_name),
    )


def clear_search_embedding_cache() -> None:
    """Drop the cached embeddings (called when the index is rebuilt)."""

    _SEARCH_EMBEDDINGS_CACHE.clear()


def session_rows(session, library_root: Path) -> list[dict]:
    """Read asset rows through the session, falling back to the index store."""

    from iPhoto.application.dtos import AssetQuery

    queries = getattr(session, "asset_queries", None)
    if queries is not None:
        try:
            rows = queries.read_query_asset_rows(Path(library_root), AssetQuery())
            if rows:
                return [row for row in rows if isinstance(row, dict)]
        except Exception:
            pass
    return read_all_asset_rows(Path(library_root))


def build_location_index(
    library_root: Path, rows: list[dict] | None = None
) -> list[tuple[str, str]]:
    """Return ``(asset_id, place label)`` for every geotagged asset.

    The ``gps`` column stores JSON text, so it has to be parsed before use; the
    place names come from the bundled offline reverse geocoder.
    """

    key = str(library_root)
    cached = _LOCATION_INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    import json

    from iPhoto.utils.geocoding import resolve_location_name

    if rows is None:
        rows = read_all_asset_rows(library_root)

    index: list[tuple[str, str]] = []
    for row in rows:
        gps = row.get("gps")
        if isinstance(gps, str):
            try:
                gps = json.loads(gps)
            except ValueError:
                continue
        if not isinstance(gps, dict):
            continue
        label = resolve_location_name(gps)
        if not label:
            continue
        asset_id = row.get("id")
        if not asset_id:
            continue
        index.append((str(asset_id), label.lower()))

    _LOCATION_INDEX_CACHE[key] = index
    return index


def read_all_asset_rows(library_root: Path) -> list[dict]:
    """Read every asset row through the bootstrap index reader."""

    from iPhoto.bootstrap.index_reader import read_all_asset_rows

    return read_all_asset_rows(library_root)


def match_location_hits(
    library_root: Path, query: str, rows: list[dict] | None = None
) -> list[tuple[str, str]]:
    """Return assets whose place name contains *query*."""

    from iPhoto.agent.services.place_aliases import aliases_for

    needles = [needle for needle in aliases_for(query) if len(needle) >= 2]
    if not needles:
        return []
    matches: list[tuple[str, str]] = []
    seen: set[str] = set()
    for asset_id, label in build_location_index(Path(library_root), rows):
        if asset_id in seen:
            continue
        if any(needle in label for needle in needles):
            seen.add(asset_id)
            matches.append((asset_id, label))
    return matches


class SearchThread(QThread):
    finished = Signal(object, str)  # results or None, error
    progress = Signal(str, int, int)  # message, current, total

    def __init__(
        self,
        query_text,
        model_path,
        lib_root,
        model_spec,
        *,
        library_session=None,
        precision="balanced",
        scope_album_path=None,
        similar_to=None,
    ):
        super().__init__()
        #: The open library session, when there is one: it owns the embedding
        #: service the startup preload already warmed, so a search does not
        #: decode the model a second time.
        self._library_session = library_session
        self._model_spec = model_spec
        self._query = query_text
        self._model_dir = model_path
        self._library_root = lib_root
        self._precision = precision
        # Library-relative album path when the search is scoped to the
        # album currently open; ``None`` searches the whole library.
        self._scope_album_path = scope_album_path
        # When set, rank by visual similarity to this asset instead of by
        # a text query ("find similar photos").
        self._similar_to = similar_to

    def run(self):
        try:
            from iPhoto.agent.infrastructure.clip_embedding import CLIPEmbeddingService
            from iPhoto.bootstrap.embedding_index import load_stored_embeddings
            from iPhoto.agent.services import search_models
            import time

            # Reuse the session's embedding service when the weights are
            # installed: it is also the instance the startup preload warms,
            # so a search does not decode the 577 MB model a second time.
            # Guard on availability first: ``get_embedding_service`` opens a
            # download dialog, which must not happen on this worker thread.
            session_service = None
            if self._library_session is not None:
                try:
                    if search_models.is_installed(self._model_spec):
                        session_service = (
                            self._library_session.get_embedding_service()
                        )
                except Exception:
                    session_service = None
            probe = (
                getattr(session_service, "is_loaded", None)
                if session_service is not None
                else None
            )
            already_loaded = bool(probe()) if callable(probe) else False
            self.progress.emit(
                "正在搜索..." if already_loaded else "正在加载 AI 模型...", 0, 0
            )
            service = session_service
            if service is None:
                service = CLIPEmbeddingService(
                    model_dir=self._model_dir,
                    family=getattr(self._model_spec, "family", "clip"),
                )
            loader = getattr(service, "load", None)
            if callable(loader):
                loader()
            else:
                service._ensure_loaded()

            if self.isInterruptionRequested():
                return

            if not service.is_loaded():
                self.finished.emit(None, "AI 模型加载失败")
                return

            self.progress.emit("AI 模型加载完成，正在加载索引...", 1, 3)

            # 2. Load embeddings
            from iPhoto.agent.services import search_models as _search_models

            all_embeddings = load_search_embeddings(
                self._library_root,
                model_name=self._model_spec.dir_name,
            )

            if not all_embeddings:
                self.finished.emit(None, "没有找到照片索引，请先生成索引")
                return

            self.progress.emit(f"已加载 {len(all_embeddings)} 个索引，正在搜索...", 2, 3)

            # Scope: "当前相册" keeps only the embeddings of the album the
            # user is looking at.
            if self._scope_album_path:
                try:
                    from iPhoto.domain.models.query import AssetQuery

                    query_service = (
                        getattr(self._library_session, "asset_queries", None)
                        if self._library_session is not None
                        else None
                    )
                    if query_service is not None:
                        scoped_rows = query_service.read_query_asset_rows(
                            Path(self._library_root),
                            AssetQuery(
                                album_path=self._scope_album_path,
                                include_subalbums=True,
                            ),
                        )
                        scoped_ids = {
                            row.get("id")
                            for row in scoped_rows
                            if isinstance(row, dict)
                        }
                        all_embeddings = [
                            item
                            for item in all_embeddings
                            if item["asset_id"] in scoped_ids
                        ]
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Album scope filter failed", exc_info=True
                    )
                if not all_embeddings:
                    self.finished.emit(None, "当前相册里没有可搜索的照片")
                    return

            # Adaptive cutoff: keep the head of *this* query's score
            # distribution instead of one global threshold (0.24 admitted
            # 366 "dog" photos and 653 "cat" ones on a real library).
            # Animal queries additionally require the query to beat the
            # best-scoring other animal, which is the zero-shot
            # "is this more a dog than any other animal?" decision.
            import os as _os

            import numpy as _np

            from iPhoto.agent.services.search_ranking import (
                needs_translation as _needs_translation,
                labels_for_query,
                other_labels,
                precision_params,
                select_results,
                translate_query,
                translation_is_partial,
            )


            matrix = _np.stack([item["embedding"] for item in all_embeddings])

            if self._similar_to:
                query_index = next(
                    (
                        index
                        for index, item in enumerate(all_embeddings)
                        if item["asset_id"] == self._similar_to
                    ),
                    None,
                )
                if query_index is None:
                    self.finished.emit(None, "找不到这张照片的向量")
                    return
                query_embedding = all_embeddings[query_index]["embedding"]
                sim_scores = matrix @ query_embedding
                # The source photo itself is always the perfect match.
                sim_scores[query_index] = -1.0
                keep_indices = select_results(
                    sim_scores, floor=0.65, margin=0.08
                )
            else:
                _family = self._model_spec.family
                # Only the English-only CLIP tower needs the built-in
                # glossary. SigLIP embeds Chinese natively, and the
                # glossary drops meaning there ("茉酸奶" → "yogurt"),
                # so the query is encoded exactly as typed.
                if _needs_translation(_family):
                    translated_query = translate_query(self._query)
                    partial_translation = translation_is_partial(self._query)
                else:
                    translated_query = self._query
                    partial_translation = False

                query_embedding = service.encode_text(translated_query)
                if query_embedding is None:
                    self.finished.emit(None, "搜索编码失败")
                    return

                sim_scores = matrix @ query_embedding
                floor, margin, margin_tolerance, zscore = precision_params(
                    self._precision,
                    family=_family,
                )
                try:
                    settings = SearchSettings.from_env()
                    if settings.min_similarity_override is not None:
                        floor = settings.min_similarity_override
                except Exception:
                    pass

                if partial_translation:
                    # Untranslated or partly translated Chinese leaves
                    # the model guessing, so keep only the head of the
                    # distribution. These margins are relative to the
                    # family's floor: the old absolute 0.265/0.28 were
                    # CLIP-scale numbers and rejected every SigLIP hit
                    # (its matches peak near 0.13), which turned real
                    # queries into 0 photos.
                    base_floor = floor
                    floor = max(floor, base_floor * 1.10)
                    margin = min(margin, margin * 0.6)
                    if translated_query == self._query:
                        floor = max(floor, base_floor * 1.17)
                        margin = min(margin, margin * 0.33)

                margins = None
                group = labels_for_query(translated_query)
                if group is not None and margin_tolerance is not None:
                    other_vectors = [
                        vector
                        for label in other_labels(group)
                        if (
                            vector := service.encode_text_cached(label)
                        )
                        is not None
                    ]
                    if other_vectors:
                        other_scores = matrix @ _np.stack(other_vectors).T
                        margins = sim_scores - other_scores.max(axis=1)

                keep_indices = select_results(
                    sim_scores,
                    floor=floor,
                    margin=margin,
                    margins=margins,
                    margin_tolerance=margin_tolerance,
                    zscore=zscore,
                )

            results = [
                {
                    "asset_id": all_embeddings[index]["asset_id"],
                    "score": float(sim_scores[index]),
                }
                for index in keep_indices
            ]

            # OCR text search (if available). Similar-photo searches are
            # purely visual, so they skip the text index.
            if self._similar_to is None:
                try:
                    from iPhoto.ai.db.ocr_database import get_ocr_repository

                    ocr_repo = get_ocr_repository(self._library_root)
                    ocr_hits = ocr_repo.search(self._query, limit=200)
                    seen_ids = {r["asset_id"] for r in results}
                    for hit in ocr_hits:
                        if hit.asset_id not in seen_ids:
                            results.append(
                                {
                                    "asset_id": hit.asset_id,
                                    "score": min(hit.confidence, 1.0),
                                    "source": "ocr",
                                }
                            )
                            seen_ids.add(hit.asset_id)
                    results.sort(key=lambda x: x["score"], reverse=True)
                except Exception:
                    pass

            # Location search: a plain text query also matches the
            # reverse-geocoded place name ("武汉", "Tokyo"), so "武汉"
            # finds geotagged photos even when the image model does not
            # associate the scene with the city.
            if self._similar_to is None:
                try:
                    location_hits = match_location_hits(
                        self._library_root,
                        self._query,
                        session_rows(self._library_session, self._library_root),
                    )
                    seen_ids = {r["asset_id"] for r in results}
                    for asset_id, label in location_hits:
                        if asset_id in seen_ids:
                            continue
                        results.append(
                            {
                                "asset_id": asset_id,
                                "score": 0.0,
                                "source": "location",
                                "location": label,
                            }
                        )
                        seen_ids.add(asset_id)
                except Exception:
                    logging.getLogger(__name__).debug(
                        "Location search failed", exc_info=True
                    )

            # Keep the whole match set: the cutoff already decides what
            # belongs here, and the gallery loads result windows lazily.

            self.finished.emit(results, "")

        except Exception as e:
            logging.getLogger(__name__).error("Search failed: %s", e)
            self.finished.emit(None, str(e))


__all__ = [
    "SearchThread",
    "build_location_index",
    "clear_search_embedding_cache",
    "load_search_embeddings",
    "match_location_hits",
    "read_all_asset_rows",
    "session_rows",
]
