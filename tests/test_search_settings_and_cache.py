"""Search settings and the embedding cache behave predictably."""

from __future__ import annotations

import pytest

from iPhoto.application.search.embedding_cache import EmbeddingCache
from iPhoto.config import SEARCH_MAX_RESULTS, SEARCH_MIN_SIMILARITY, SearchSettings


def test_defaults_match_the_measured_values() -> None:
    settings = SearchSettings.from_env({})

    assert settings.min_similarity == SEARCH_MIN_SIMILARITY
    assert settings.max_results == SEARCH_MAX_RESULTS


def test_environment_overrides_are_applied() -> None:
    settings = SearchSettings.from_env(
        {"IPHOTO_SEARCH_MIN_SIM": "0.30", "IPHOTO_SEARCH_MAX_RESULTS": "50"}
    )

    assert settings.min_similarity == 0.30
    assert settings.max_results == 50


def test_invalid_overrides_fall_back_to_defaults() -> None:
    settings = SearchSettings.from_env(
        {"IPHOTO_SEARCH_MIN_SIM": "not-a-number", "IPHOTO_SEARCH_MAX_RESULTS": "x"}
    )

    assert settings.min_similarity == SEARCH_MIN_SIMILARITY
    assert settings.max_results == SEARCH_MAX_RESULTS


def test_cache_loads_once_per_mtime() -> None:
    cache = EmbeddingCache()
    calls: list[int] = []

    def loader() -> list:
        calls.append(1)
        return ["embedding"]

    first = cache.get("library", 100.0, loader)
    second = cache.get("library", 100.0, loader)

    assert first == second == ["embedding"]
    assert len(calls) == 1, "the same mtime must not reload"


def test_cache_reloads_when_the_source_changes() -> None:
    cache = EmbeddingCache()
    calls: list[int] = []

    def loader() -> list:
        calls.append(1)
        return [len(calls)]

    cache.get("library", 100.0, loader)
    fresh = cache.get("library", 200.0, loader)

    assert len(calls) == 2
    assert fresh == [2]


def test_unknown_mtime_is_not_cached() -> None:
    cache = EmbeddingCache()
    calls: list[int] = []

    def loader() -> list:
        calls.append(1)
        return []

    cache.get("library", 0.0, loader)
    cache.get("library", 0.0, loader)

    assert len(calls) == 2, "without an mtime there is no way to detect staleness"


def test_invalidate_forces_a_reload() -> None:
    cache = EmbeddingCache()
    calls: list[int] = []

    def loader() -> list:
        calls.append(1)
        return []

    cache.get("library", 100.0, loader)
    cache.invalidate("library")
    cache.get("library", 100.0, loader)

    assert len(calls) == 2
