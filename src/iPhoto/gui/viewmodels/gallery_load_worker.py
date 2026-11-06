"""Background worker for gallery data loading (DB count + fetch)."""

from __future__ import annotations

import copy
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from ...utils.logging import diag

from .asset_dto_converter import (
    scan_row_is_thumbnail as _scan_row_is_thumbnail_fn,
    scan_row_to_dto as _scan_row_to_dto_fn,
)

LOGGER = logging.getLogger(__name__)


class GalleryLoadSignals(QObject):
    """Signals emitted by :class:`GalleryLoadWorker`."""

    finished = Signal(int, object, int)   # generation, row_cache, total_count
    error = Signal(int, str)            # generation, error_message


class GalleryLoadWorker(QRunnable):
    """Run gallery DB queries on a background thread.

    Performs ``count_query_assets`` + ``read_query_asset_rows`` + DTO
    conversion without blocking the main thread.
    """

    def __init__(
        self,
        generation: int,
        asset_query_service: Any,
        active_root: Path,
        query: AssetQuery,
        first: int,
        last: int,
        signals: GalleryLoadSignals,
        *,
        validate_paths: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._generation = generation
        self._asset_query_service = asset_query_service
        self._active_root = active_root
        self._query = copy.deepcopy(query)
        self._first = first
        self._last = last
        self._signals = signals
        self._validate_paths = validate_paths
        self._is_cancelled = False
        # Timing instrumentation (emitted only with IPHOTO_DIAG=1): how long the task
        # sat in the pool queue and how long it actually ran.
        self._queued_at = time.monotonic()

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        started = time.monotonic()
        try:
            if self._is_cancelled:
                diag(
                    "[DIAG] load worker gen=%s cancelled in queue after %.0f ms",
                    self._generation, (started - self._queued_at) * 1000,
                )
                return

            # 1. COUNT query
            count_query = self._count_query(self._query)
            t_count = time.monotonic()
            total = self._asset_query_service.count_query_assets(count_query)
            t_count = time.monotonic() - t_count

            if self._is_cancelled:
                return

            if total <= 0:
                self._signals.finished.emit(self._generation, {}, 0)
                return

            # 2. Fetch rows for the requested window
            first = max(0, min(self._first, total - 1))
            last = max(first, min(self._last, total - 1))
            query = self._slice_query(self._query, first, last - first + 1)

            row_cache: Dict[int, AssetDTO] = {}
            t_rows = 0.0
            t_convert = 0.0
            fetch_started = time.monotonic()
            rows_iter = self._asset_query_service.read_query_asset_rows(self._active_root, query)
            for offset, row in enumerate(rows_iter):
                if offset == 0:
                    t_rows = time.monotonic() - fetch_started
                if self._is_cancelled:
                    return
                row_index = first + offset
                view_rel = row.get("rel") if isinstance(row, dict) else None
                if not isinstance(view_rel, str) or not view_rel:
                    continue
                if _scan_row_is_thumbnail_fn(view_rel, row):
                    continue
                convert_started = time.monotonic()
                dto = _scan_row_to_dto_fn(self._active_root, view_rel, row)
                t_convert += time.monotonic() - convert_started
                if dto is None:
                    continue
                row_cache[row_index] = dto
            if t_rows == 0.0:
                t_rows = time.monotonic() - fetch_started

            if self._is_cancelled:
                return

            # 3. Path validation (moved from main thread)
            t_validate = 0.0
            validate_started = time.monotonic()
            if self._validate_paths and row_cache:
                row_cache = {
                    k: v for k, v in row_cache.items()
                    if os.path.exists(v.abs_path)
                }

            t_validate = time.monotonic() - validate_started

            self._signals.finished.emit(self._generation, row_cache, total)
            diag(
                "[DIAG] load worker gen=%s: queue %.0f | count %.0f | rows %.0f | "
                "convert %.0f | validate %.0f | total %.0f ms | rows=%d",
                self._generation,
                (started - self._queued_at) * 1000,
                t_count * 1000,
                t_rows * 1000,
                t_convert * 1000,
                t_validate * 1000,
                (time.monotonic() - started) * 1000,
                len(row_cache),
            )

        except Exception as exc:
            LOGGER.error("GalleryLoadWorker failed: %s", exc, exc_info=True)
            if not self._is_cancelled:
                self._signals.error.emit(self._generation, str(exc))

    @staticmethod
    def _count_query(query: AssetQuery) -> AssetQuery:
        count_query = copy.deepcopy(query)
        count_query.offset = 0
        count_query.limit = None
        return count_query

    @staticmethod
    def _slice_query(query: AssetQuery, offset: int, limit: int) -> AssetQuery:
        sliced = copy.deepcopy(query)
        sliced.offset = offset
        sliced.limit = limit
        return sliced
