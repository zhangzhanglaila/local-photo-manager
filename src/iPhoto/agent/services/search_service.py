"""Semantic search service using CLIP."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

import numpy as np

from ..models.search_result import SearchResult

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TOP_K = 50
_MIN_SIMILARITY_THRESHOLD = 0.2


class SearchService:
    """Semantic search service using CLIP.

    This service provides natural language search capabilities
    using CLIP embeddings for semantic understanding.
    """

    def __init__(
        self,
        embedding_service,
        asset_repository,
        embedding_repository,
        ocr_repository=None,
    ) -> None:
        """Initialize the search service.

        Parameters
        ----------
        embedding_service : CLIPEmbeddingService
            Service for generating embeddings.
        asset_repository : object
            Repository for accessing asset data.
        embedding_repository : object
            Repository for storing and retrieving embeddings.
        ocr_repository : object, optional
            OCR repository for text-based search.
            Repository for storing and retrieving embeddings.
        """
        self._embedding_service = embedding_service
        self._asset_repository = asset_repository
        self._embedding_repository = embedding_repository
        self._ocr_repository = ocr_repository

    def search(
        self,
        query: str,
        top_k: int = _DEFAULT_TOP_K,
        threshold: float = _MIN_SIMILARITY_THRESHOLD,
    ) -> List[SearchResult]:
        """Search for photos matching a natural language query.

        Parameters
        ----------
        query : str
            Natural language search query.
        top_k : int
            Maximum number of results to return.
        threshold : float
            Minimum similarity score.

        Returns
        -------
        List[SearchResult]
            List of matching assets, sorted by relevance.
        """
        import time
        start_time = time.time()

        if not query.strip():
            return []

        # Generate text embedding for the query
        query_embedding = self._embedding_service.encode_text(query)
        if query_embedding is None:
            _LOGGER.warning("Failed to generate embedding for query: %s", query)
            return []

        # Only vectors from the active model: other models live in another space.
        from . import search_models

        all_embeddings = self._embedding_repository.get_all_embeddings(
            model_name=search_models.active_model().dir_name
        )
        _LOGGER.info("Loaded %d embeddings in %.3f seconds", len(all_embeddings), time.time() - start_time)

        if not all_embeddings:
            _LOGGER.info("No embeddings found. Please run embedding generation first.")
            return []

        # Compute similarities
        results = []
        for item in all_embeddings:
            asset_id = item["asset_id"]
            stored_embedding = item["embedding"]

            if stored_embedding is None:
                continue

            similarity = self._embedding_service.compute_similarity(
                query_embedding, stored_embedding
            )

            if similarity >= threshold:
                results.append(SearchResult(
                    asset_id=asset_id,
                    asset_rel="",
                    score=float(similarity),
                ))

        # OCR text search (if available)
        if self._ocr_repository is not None:
            try:
                ocr_hits = self._ocr_repository.search(query, limit=top_k)
                seen_ids = {r.asset_id for r in results}
                for hit in ocr_hits:
                    if hit.asset_id not in seen_ids:
                        results.append(SearchResult(
                            asset_id=hit.asset_id,
                            asset_rel=hit.asset_rel,
                            score=min(hit.confidence, 1.0),
                            caption=hit.snippet,
                            metadata={"source": "ocr"},
                        ))
                        seen_ids.add(hit.asset_id)
            except Exception:
                pass

        # Sort by similarity (highest first)
        results.sort(key=lambda x: x.score, reverse=True)

        # Limit results
        results = results[:top_k]

        # Enrich with asset metadata
        if results:
            asset_ids = [r.asset_id for r in results]
            asset_rows = self._asset_repository.get_rows_by_ids(asset_ids)
            row_map = {row["id"]: row for row in asset_rows}

            for result in results:
                row = row_map.get(result.asset_id)
                if row:
                    result.asset_rel = row.get("rel", "")

        elapsed = time.time() - start_time
        _LOGGER.info("Search completed: %d results in %.3f seconds", len(results), elapsed)

        return results
