"""Ranking rules behind the search precision tiers."""

from __future__ import annotations

import numpy as np

from iPhoto.agent.infrastructure.clip_embedding import CLIPEmbeddingService
from iPhoto.agent.services.search_ranking import (
    labels_for_query,
    other_labels,
    precision_params,
    select_results,
)


def test_labels_for_query_matches_english_and_chinese() -> None:
    assert labels_for_query("dog") == ("dog", "puppy")
    assert labels_for_query("a puppy running") == ("dog", "puppy")
    assert labels_for_query("小狗") == ("dog", "puppy")
    assert labels_for_query("cat") == ("cat", "kitten")
    assert labels_for_query("beach") is None
    # Whole-word match only: "hotdog" is not an animal query.
    assert labels_for_query("hotdog") is None


def test_other_labels_excludes_the_query_group() -> None:
    others = other_labels(("dog", "puppy"))

    assert "dog" not in others
    assert "puppy" not in others
    assert "cat" in others
    assert "pet" not in others


def test_adaptive_cutoff_keeps_the_head_and_applies_cap() -> None:
    scores = np.array([0.30, 0.27, 0.25, 0.24, 0.10], dtype=np.float32)

    indices = select_results(scores, floor=0.20, margin=0.06, cap=3)

    assert indices == [0, 1, 2]


def test_floor_rejects_queries_whose_best_match_is_too_weak() -> None:
    scores = np.array([0.22, 0.221, 0.219], dtype=np.float32)

    assert select_results(scores, floor=0.24, margin=0.06, cap=10) == []


def test_margin_filter_drops_other_animals() -> None:
    scores = np.array([0.29, 0.28, 0.27], dtype=np.float32)
    margins = np.array([0.03, 0.02, -0.02], dtype=np.float32)

    indices = select_results(
        scores,
        floor=0.20,
        margin=0.05,
        cap=10,
        margins=margins,
        margin_tolerance=0.0,
    )

    assert indices == [0, 1]


def test_margin_tolerance_none_disables_the_filter() -> None:
    scores = np.array([0.29, 0.28], dtype=np.float32)
    margins = np.array([0.03, -0.02], dtype=np.float32)

    indices = select_results(
        scores,
        floor=0.20,
        margin=0.05,
        cap=10,
        margins=margins,
        margin_tolerance=None,
    )

    assert indices == [0, 1]


def test_precision_params_fall_back_to_balanced() -> None:
    assert precision_params("strict")[0] == 0.25
    assert precision_params("unknown") == precision_params("balanced")


def test_text_vectors_are_cached_across_calls() -> None:
    service = CLIPEmbeddingService.__new__(CLIPEmbeddingService)
    service._text_cache = {}
    calls: list[str] = []

    def _encode(text: str):
        calls.append(text)
        return np.ones(4, dtype=np.float32)

    service.encode_text = _encode  # type: ignore[method-assign]

    first = service.encode_text_cached("dog")
    second = service.encode_text_cached("dog")

    assert calls == ["dog"]
    assert first is second
