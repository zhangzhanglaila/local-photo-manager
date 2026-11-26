"""Timeline buckets for the gallery's year rail and date pill.

The gallery store used to own both the bucket math and the cache it needs, which
put four fields and roughly 140 lines of date arithmetic in the middle of a
pagination class. The logic only ever reads a query, a total count and the row
cache, so it lives here: the store keeps the public API and passes the view state
straight through — no wrapper object per call, because ``month_for_row`` runs on
every timeline-scrub event and the rail asks for buckets on every repaint.

Two things matter for correctness, both learned the hard way upstream:

* the cache is keyed by the query's *content* (``repr``) rather than its
  ``id()`` — a recreated ``AssetQuery`` can reuse a freed id, and with an
  unchanged total the rail then kept showing the previous view's years;
* buckets are aggregated per year before building spans, because a
  non-contiguous same-year bucket (mixed sort order, a scan inserting an old
  row) otherwise produced two spans for the same year and the rail drew the
  label twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)


def bucket_cache_key(query: Any, generation: int, total_count: int) -> tuple:
    """Return the content key under which buckets for one view are cached."""

    if query is None:
        return ("direct", generation, total_count)
    return (repr(query), total_count)


class GalleryTimelineIndex:
    """Cache ``(year, month, count)`` buckets and answer row lookups."""

    def __init__(self) -> None:
        self._buckets: list[tuple[int, int, int]] = []
        self._cache_key: tuple | None = None
        self._offsets: list[int] = []
        self._offsets_key: tuple[int, int] | None = None

    def invalidate(self) -> None:
        """Drop every cached bucket and offset."""

        self._buckets = []
        self._cache_key = None
        self._offsets = []
        self._offsets_key = None

    def buckets(
        self,
        *,
        query: Any,
        generation: int,
        total_count: int,
        direct_mode: bool,
        row_cache: Mapping[int, Any],
        count_buckets: Callable[[Any], Iterable[tuple[int, int, int]]] | None,
    ) -> list[tuple[int, int, int]]:
        """Return ``(year, month, count)`` for the view, newest first.

        Cached per query so opening the timeline rail costs one small aggregate
        query per view change, not one per repaint.
        """

        if query is None and not direct_mode:
            return []
        if total_count <= 0:
            return []

        cache_key = bucket_cache_key(query, generation, total_count)
        if self._cache_key == cache_key:
            return list(self._buckets)

        if query is None:
            buckets = self._buckets_from_row_cache(row_cache)
        else:
            buckets = self._buckets_from_query(query, count_buckets)

        self._cache_key = cache_key
        self._buckets = buckets
        return list(buckets)

    def month_for_row(
        self,
        row: int,
        *,
        buckets: list[tuple[int, int, int]],
        total_count: int,
    ) -> tuple[int, int] | None:
        """Return ``(year, month)`` containing *row*, using only cached buckets.

        Dragging the timeline must not query the database, so the date pill asks
        this instead of loading the row. Precision is the month — enough to show
        a live preview while the pointer moves.
        """

        if not buckets or row < 0:
            return None

        # Cumulative offsets are rebuilt only when the bucket list changes.
        offsets_key = (len(buckets), total_count)
        if self._offsets_key != offsets_key:
            offsets: list[int] = []
            consumed = 0
            for _year, _month, count in buckets:
                offsets.append(consumed)
                consumed += max(0, count)
            self._offsets = offsets
            self._offsets_key = offsets_key

        offsets = self._offsets
        lo, hi = 0, len(offsets) - 1
        found = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if offsets[mid] <= row:
                found = mid
                lo = mid + 1
            else:
                hi = mid - 1
        year, month, _count = buckets[found]
        return int(year), int(month)

    def year_spans(
        self,
        *,
        buckets: list[tuple[int, int, int]],
        total_count: int,
    ) -> list[tuple[int, int, int]]:
        """Return ``(year, first_row, count)`` in display order (newest first).

        The rail sizes each year by ``count`` so a year holding thousands of
        photos occupies far more of the track than a year holding three. Rows are
        sorted by date descending, so cumulative bucket counts give the exact row
        each year starts at — no scanning of asset rows required.
        """

        if not buckets:
            return []

        per_year: dict[int, int] = {}
        for year, _month, count in buckets:
            try:
                key = int(year)
            except (TypeError, ValueError):
                continue
            per_year[key] = per_year.get(key, 0) + max(0, int(count))
        if not per_year:
            return []

        result: list[tuple[int, int, int]] = []
        row = 0
        for year in sorted(per_year, reverse=True):
            count = per_year[year]
            first_row = min(row, max(0, total_count - 1))
            result.append((year, first_row, count))
            row += count
        return result

    def year_start_rows(
        self,
        *,
        buckets: list[tuple[int, int, int]],
        total_count: int,
    ) -> list[tuple[int, int]]:
        """Return ``(year, first_row)`` pairs in display order (newest first)."""

        return [
            (year, first_row)
            for year, first_row, _count in self.year_spans(
                buckets=buckets,
                total_count=total_count,
            )
        ]

    @staticmethod
    def _buckets_from_row_cache(
        row_cache: Mapping[int, Any],
    ) -> list[tuple[int, int, int]]:
        """Group the in-memory DTOs by ``(year, month)``, newest first."""

        counts: dict[tuple[int, int], int] = {}
        for dto in row_cache.values():
            created_at = getattr(dto, "created_at", None)
            if created_at is None:
                continue
            try:
                key = (int(created_at.year), int(created_at.month))
            except (AttributeError, TypeError, ValueError):
                continue
            counts[key] = counts.get(key, 0) + 1
        return [
            (year, month, count)
            for (year, month), count in sorted(counts.items(), reverse=True)
        ]

    @staticmethod
    def _buckets_from_query(
        query: Any,
        getter: Callable[[Any], Iterable[tuple[int, int, int]]] | None,
    ) -> list[tuple[int, int, int]]:
        if getter is None:
            return []
        try:
            return [
                (int(year), int(month), int(count))
                for year, month, count in getter(query)
            ]
        except Exception:
            logger.warning("Timeline buckets unavailable", exc_info=True)
            return []


__all__ = ["GalleryTimelineIndex", "bucket_cache_key"]
