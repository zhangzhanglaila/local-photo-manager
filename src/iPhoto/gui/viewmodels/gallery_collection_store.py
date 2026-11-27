"""Pure Python collection store backing gallery and filmstrip selection."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from PySide6.QtCore import QTimer

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.signal import Signal

from iPhoto.gui.viewmodels.gallery_load_worker import (
    GalleryLoadSignals,
    GalleryLoadWorker,
)
from iPhoto.gui.viewmodels.gallery_load_window import GalleryLoadWindowMixin
from iPhoto.gui.viewmodels.gallery_mutations import GalleryMutationMixin
from iPhoto.gui.viewmodels.gallery_scan_refresh import GalleryScanRefreshMixin

from iPhoto.gui.viewmodels.asset_dto_converter import (
    geotagged_asset_to_dto as _geotagged_asset_to_dto_fn,
    is_legacy_thumb_path as _is_legacy_thumb_path_fn,
    resolve_abs_path as _resolve_abs_path_fn,
    scan_row_is_thumbnail as _scan_row_is_thumbnail_fn,
    scan_row_to_dto as _scan_row_to_dto_fn,
)
from iPhoto.gui.viewmodels.asset_paging import (
    should_validate_paths as _should_validate_paths_fn,
)
from iPhoto.gui.viewmodels.path_cache import PathExistsCache
from iPhoto.gui.viewmodels.pending_move_buffer import (
    _PendingMove,
    should_include_pending as _should_include_pending_fn,
)
from ...utils.logging import diag


class GalleryAssetQuerySurface(Protocol):
    """Session-owned query surface used by the gallery collection store."""

    library_root: Path

    def count_query_assets(self, query: AssetQuery) -> int:
        """Return the number of assets matching *query*."""

    def read_query_asset_rows(
        self,
        root: Path,
        query: AssetQuery,
    ) -> Iterable[dict]:
        """Yield rows matching *query*, scoped to *root*."""


class GalleryCollectionStore(
    GalleryLoadWindowMixin,
    GalleryScanRefreshMixin,
    GalleryMutationMixin,
):
    """Pure Python gallery data store with viewport-aware caching."""

    INITIAL_VISIBLE_ROWS = 50
    MIN_WINDOW_SIZE = 200
    MAX_WINDOW_SIZE = 1000
    WINDOW_MULTIPLIER = 3
    LOOKBEHIND_SCREENS = 1
    LOOKAHEAD_SCREENS = 2
    HYSTERESIS_RATIO = 0.25
    # Minimum spacing between scan-driven window reloads. Scan chunks arrive many
    # times per second and every reload resets the model, which itself emits
    # ``visibleRowsChanged``; without this cap the two feed each other.
    SCAN_REFRESH_MIN_INTERVAL_S = 0.15

    def __init__(
        self,
        asset_query_service: GalleryAssetQuerySurface | None,
        library_root: Optional[Path] = None,
    ) -> None:
        self.data_changed = Signal()
        self.window_changed = Signal()
        self.count_changed = Signal()
        self.row_changed = Signal()
        # Absolute row range that was just loaded. Row numbers are absolute, so a new
        # window only fills rows that already exist: the view needs `dataChanged` for
        # that range instead of a model reset.
        self.rows_loaded = Signal()

        self._asset_query_service = asset_query_service
        self._library_root = library_root or getattr(asset_query_service, "library_root", None)
        self._current_query: Optional[AssetQuery] = None
        self._selection_query: Optional[AssetQuery] = None
        self._selection_direct_assets: Optional[list] = None
        self._selection_library_root: Optional[Path] = library_root
        self._total_count = 0
        self._generation = 0
        self._row_cache: Dict[int, AssetDTO] = {}
        # Row counts only change when the query changes or a scan writes rows, yet every
        # window load re-ran the count query (29-75 ms measured). The cache is keyed by
        # the query object, so any filter/sort change misses it automatically.
        self._cached_count: int | None = None
        self._cached_count_key: str | None = None
        self._row_mapping_key: str | None = None
        # Range actually requested from the worker; used to report exactly what was
        # loaded (never min..max of the cache, which can span unloaded holes).
        self._load_window_range: tuple[int, int] | None = None
        self._emit_signals_on_load = True
        # Timing: end-to-end latency of a window load and how many requests were
        # superseded before one finished (diag-only).
        self._load_requested_at: float | None = None
        # While the user drags the timeline the main thread is saturated by repaints
        # and event handling; applying every finished load made the newest result wait
        # seconds in the queue. Throttling application keeps at most one reset per
        # interval and always applies the latest result on release.
        self._apply_throttle_s = 0.0
        self._last_apply_at = 0.0
        self._deferred_result: tuple[int, dict, int] | None = None
        self._load_superseded = 0
        self._window_range: Optional[tuple[int, int]] = None
        self._visible_range: Optional[tuple[int, int]] = None
        self._active_root: Optional[Path] = None
        self._path_cache = PathExistsCache()
        self._pending_moves: List[_PendingMove] = []
        self._pending_paths: set[str] = set()
        self._pinned_row: Optional[int] = None
        self._pending_scan_refresh = False
        self._pending_scan_rels: set[str] = set()
        self._pending_scan_sort_keys: set[tuple[str, str]] = set()
        self._last_scan_refresh_at = 0.0
        # While the user drags the timeline, scan-driven refreshes are held back: each
        # one resets the model, and combining them with the drag's own reloads left the
        # grid repainting black tiles. The pending flag keeps the data for later.
        self._scan_refresh_suppressed = False
        # While true, a finished window load is folded into the cache and reported as
        # a range refresh instead of replacing it with a model reset (see
        # ``_on_load_finished``). Used for the duration of a timeline drag so the view
        # keeps its scroll position.
        self._merge_on_apply = False
        self._direct_mode = False
        # ⭐ 跟踪在 asset_query_service 绑定后需要重新触发的加载
        self._pending_load_after_service_bind: bool = False
        self._pending_load_range: tuple[int, int] | None = None

        # Async loading state
        self._load_worker: Optional[GalleryLoadWorker] = None
        self._load_generation: int = 0
        self._load_signals: Optional[GalleryLoadSignals] = None
        self._logger = logging.getLogger(__name__)

        # Timeline (year rail): buckets are cached per query + total count.
        self._timeline_buckets: list[tuple[int, int, int]] = []
        self._timeline_cache_key: Optional[tuple[int, int]] = None
        self._bucket_offsets: list[int] = []
        self._bucket_offsets_key: tuple[int, int] | None = None

    def cancel_pending_load(self) -> None:
        """Cancel any in-flight async load."""
        if self._load_worker is not None:
            self._load_worker.cancel()
            self._load_worker = None
        self._load_generation += 1

    def set_library_root(self, root: Optional[Path]) -> None:
        if self._library_root == root:
            return
        self.cancel_pending_load()
        self._library_root = root
        self.reset_visible_window()
        self.data_changed.emit()
        # ⭐ 库根改变后，如果已有活动根和 asset_query_service，触发加载
        if (
            root is not None
            and self._active_root is not None
            and self._asset_query_service is not None
        ):
            self._load_initial_window()

    def set_asset_query_service(
        self,
        asset_query_service: GalleryAssetQuerySurface | None,
    ) -> None:
        if self._asset_query_service is asset_query_service:
            return
        self._asset_query_service = asset_query_service
        # A different service means a different data source even when the query and
        # the roots are unchanged. The cached total is keyed on those, so keeping it
        # made a rebound store report the previous row count and load only that many
        # rows — the new assets were never fetched.
        self.invalidate_count_cache()
        self.reset_visible_window()
        self.data_changed.emit()
        # ⭐ 服务刚绑定上，如果有 pending load 就触发它
        if (
            asset_query_service is not None
            and self._pending_load_after_service_bind
            and self._active_root is not None
        ):
            self._pending_load_after_service_bind = False
            first, last = self._pending_load_range or (0, self.INITIAL_VISIBLE_ROWS - 1)
            self._pending_load_range = None
            self._load_initial_window((first, last))

    def rebind_asset_query_service(
        self,
        asset_query_service: GalleryAssetQuerySurface | None,
        library_root: Optional[Path],
    ) -> None:
        self.set_asset_query_service(asset_query_service)
        self.set_library_root(library_root)

    def set_active_root(self, root: Optional[Path]) -> None:
        if self._active_root == root:
            return
        self._active_root = root
        import logging
        # ⭐ 关键修复：不管 _pending_scan_refresh 是否为 True，只要服务已绑定且
        # 当前没有正在进行的加载，就触发加载。这是修复"All Photos 启动时不显示"的根因：
        # 数据库已存在时 _pending_scan_refresh=False，导致 set_active_root 不触发加载。
        if (
            root is not None
            and self._asset_query_service is not None
            and self._current_query is not None
        ):
            if self._visible_range is None:
                self._load_initial_window()
            else:
                first, last = self._visible_range
                self._reload_window_for_visible_range(first, last, emit_signals=True)
            return
        # 保持原有逻辑作为 fallback
        if (
            root is not None
            and self._pending_scan_refresh
            and self._asset_query_service is not None
        ):
            if self._current_query is None:
                from iPhoto.domain.models.query import AssetQuery
                self._current_query = AssetQuery()
            if self._visible_range is None:
                self._load_initial_window()
            else:
                first, last = self._visible_range
                self._reload_window_for_visible_range(first, last, emit_signals=True)

    def active_root(self) -> Optional[Path]:
        return self._active_root

    def library_root(self) -> Optional[Path]:
        return self._library_root

    def current_query(self) -> Optional[AssetQuery]:
        return self._clone_query(self._selection_query) if self._selection_query is not None else None

    def current_direct_assets(self) -> Optional[list]:
        return list(self._selection_direct_assets) if self._selection_direct_assets is not None else None

    def load_selection(
        self,
        active_root: Optional[Path],
        *,
        query: Optional[AssetQuery] = None,
        direct_assets: Optional[list] = None,
        library_root: Optional[Path] = None,
    ) -> None:
        has_query = query is not None
        has_direct_assets = direct_assets is not None
        if has_query == has_direct_assets:
            raise ValueError("Exactly one of query or direct_assets must be provided.")

        self.set_active_root(active_root)
        if query is not None:
            self._load_query(query)
            return

        resolved_library_root = library_root or self._library_root or active_root
        if resolved_library_root is None:
            raise ValueError("library_root is required when loading direct assets.")
        self._load_direct_assets(list(direct_assets or []), resolved_library_root)

    def reload_current_selection(self) -> None:
        if self._selection_direct_assets is not None:
            self._load_direct_assets(
                list(self._selection_direct_assets),
                self._selection_library_root or self._library_root,
            )
            return
        if self._selection_query is not None:
            self._load_query(self._selection_query)

    def _load_query(self, query: AssetQuery) -> None:
        import logging
        self._selection_query = self._clone_query(query)
        self._selection_direct_assets = None
        self._selection_library_root = self._library_root
        self._current_query = self._clone_query(query)
        self._direct_mode = False
        diag(
            "[DIAG] _load_query: active_root=%s library_root=%s service=%s query=%s",
            self._active_root, self._library_root,
            self._asset_query_service is not None, query,
        )
        self.reset_visible_window()
        self._load_initial_window()

    def _load_direct_assets(self, assets: list, library_root: Path) -> None:
        old_total = self._total_count
        stored_assets = list(assets)
        self._selection_query = None
        self._selection_direct_assets = stored_assets
        self._selection_library_root = library_root
        self._current_query = None
        self._library_root = library_root
        self._direct_mode = True
        self.reset_visible_window()

        next_index = 0
        for asset in stored_assets:
            dto = self._geotagged_asset_to_dto(asset, library_root)
            if dto is not None:
                self._row_cache[next_index] = dto
                next_index += 1

        self._total_count = len(self._row_cache)
        if self._total_count > 0:
            self._window_range = (0, self._total_count - 1)
            self._visible_range = self._window_range

        self._emit_refresh(old_total)

    def _emit_refresh(self, old_total: int) -> None:
        if old_total != self._total_count:
            self.count_changed.emit(old_total, self._total_count)
        self.data_changed.emit()
        if self._window_range is not None:
            self.window_changed.emit(*self._window_range)

    def peek_asset(self, index: int) -> Optional[AssetDTO]:
        """Return the cached DTO for *index* without touching the database.

        The grid asks for every visible row on every repaint. ``asset_at`` falls
        back to a synchronous query for rows it has not loaded yet, so a large
        scroll jump — crossing several months, for example — queried the database
        once per newly visible row while the view was trying to repaint, which
        showed up as stutter. Repaint paths should use this method and let
        :meth:`request_row` fill the cache from the event loop.
        """

        return self._row_cache.get(index)

    def request_row(self, row: int) -> None:
        """Queue one uncached row so the event loop loads it without blocking."""

        if row < 0 or row >= self._total_count or row in self._row_cache:
            return
        queue = getattr(self, "_preload_queue", None)
        if queue:
            if row not in queue:
                queue.append(row)
            return
        self._preload_queue = [row]
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._preload_next)

    def asset_at(self, index: int) -> Optional[AssetDTO]:
        dto = self._row_cache.get(index)
        if dto is not None or self._direct_mode:
            return dto
        if index < 0 or index >= self._total_count:
            return None
        if self._visible_range is not None:
            visible_first, visible_last = self._visible_range
            if visible_first <= index <= visible_last:
                self._ensure_row_loaded(index, emit_signals=False)
                return self._row_cache.get(index)
        if self._pinned_row == index:
            self._ensure_row_loaded(index, emit_signals=False)
            return self._row_cache.get(index)
        # Broad queries like All Photos may request rows outside the initial
        # paging window before the view emits a new visible-range signal.
        # Fetching on demand keeps those items interactive instead of
        # rendering as inert black cells.
        self._ensure_row_loaded(index, emit_signals=False)
        return self._row_cache.get(index)

    def find_dto_by_path(self, path: Path) -> Optional[AssetDTO]:
        target = self._normalize_abs_key(path)
        for dto in self._row_cache.values():
            if self._normalize_abs_key(dto.abs_path) == target:
                return dto
        if self._selection_direct_assets is not None:
            for asset in self._selection_direct_assets:
                dto = self._geotagged_asset_to_dto(
                    asset,
                    self._selection_library_root or self._library_root or path.parent,
                )
                if dto is not None and self._normalize_abs_key(dto.abs_path) == target:
                    return dto
        return None

    def row_for_path(self, path: Path) -> Optional[int]:
        target = self._normalize_abs_key(path)
        for row, dto in self._row_cache.items():
            if self._normalize_abs_key(dto.abs_path) == target:
                return row

        if self._selection_direct_assets is not None:
            library_root = self._selection_library_root or self._library_root
            if library_root is None:
                return None
            next_index = 0
            for asset in self._selection_direct_assets:
                dto = self._geotagged_asset_to_dto(asset, library_root)
                if dto is None:
                    continue
                if self._normalize_abs_key(dto.abs_path) == target:
                    return next_index
                next_index += 1
            return None

        if self._current_query is None or self._total_count <= 0:
            return None

        batch_size = 200
        try:
            for offset in range(0, self._total_count, batch_size):
                batch = self._fetch_rows(offset, min(self._total_count - 1, offset + batch_size - 1))
                for row, dto in batch.items():
                    if self._normalize_abs_key(dto.abs_path) == target:
                        self._row_cache.setdefault(row, dto)
                        return row
        except Exception:
            logging.getLogger(__name__).exception("row_for_path batch fetch failed")
        return None

    def count(self) -> int:
        return self._total_count

    def timeline_buckets(self) -> list[tuple[int, int, int]]:
        """Return ``(year, month, count)`` for the current query, newest first.

        Cached per query so opening the timeline rail costs one small aggregate
        query per view change, not one per repaint.
        """

        query = self._current_query
        if query is None and not self._direct_mode:
            return []
        if self._total_count <= 0:
            return []

        # Direct mode already holds every DTO in memory, so grouping it costs a
        # single pass and no SQL at all. Query mode asks the index for one
        # aggregate instead of loading rows.
        # Key on the query's *content*, not its id(): a new AssetQuery can reuse
        # the id of a garbage-collected one, and with an unchanged total the rail
        # then kept the previous view's years (an album showed the library's
        # 1970/2021… ladder instead of its own 2024).
        cache_key: tuple = (
            ("direct", self._generation, self._total_count)
            if query is None
            else (repr(query), self._total_count)
        )
        if self._timeline_cache_key == cache_key:
            return list(self._timeline_buckets)

        if query is None:
            buckets = self._buckets_from_row_cache()
        else:
            getter = getattr(self._asset_query_service, "count_query_buckets", None)
            buckets = []
            if callable(getter):
                try:
                    buckets = [
                        (int(year), int(month), int(count))
                        for year, month, count in getter(query)
                    ]
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Timeline buckets unavailable", exc_info=True
                    )
                    buckets = []

        self._timeline_cache_key = cache_key
        self._timeline_buckets = buckets
        return list(buckets)

    def _buckets_from_row_cache(self) -> list[tuple[int, int, int]]:
        """Group the in-memory DTOs by ``(year, month)``, newest first."""

        counts: dict[tuple[int, int], int] = {}
        for dto in self._row_cache.values():
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

    def month_for_row(self, row: int) -> tuple[int, int] | None:
        """Return ``(year, month)`` containing *row*, using only cached buckets.

        Dragging the timeline must not query the database, so the date pill asks
        this instead of loading the row. Precision is the month — enough to show a
        live preview while the pointer moves.
        """

        buckets = self.timeline_buckets()
        if not buckets or row < 0:
            return None

        # Cumulative offsets are rebuilt only when the bucket list changes.
        if self._bucket_offsets_key != (len(buckets), self._total_count):
            offsets: list[int] = []
            consumed = 0
            for _year, _month, count in buckets:
                offsets.append(consumed)
                consumed += max(0, count)
            self._bucket_offsets = offsets
            self._bucket_offsets_key = (len(buckets), self._total_count)

        offsets = self._bucket_offsets
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

    def year_spans(self) -> list[tuple[int, int, int]]:
        """Return ``(year, first_row, count)`` in display order (newest first).

        The rail sizes each year by ``count`` so a year holding thousands of
        photos occupies far more of the track than a year holding three. Rows are
        sorted by date descending, so cumulative bucket counts give the exact row
        each year starts at — no scanning of asset rows required.
        """

        buckets = self.timeline_buckets()
        if not buckets:
            return []

        # Aggregate per year first: the bucket query is month-ordered, but any
        # non-contiguous same-year bucket (mixed sort order, a scan inserting an
        # old row) used to create a second span for the same year, which the rail
        # then drew as a repeated label ("2025" twice).
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
            first_row = min(row, max(0, self._total_count - 1))
            result.append((year, first_row, count))
            row += count
        return result

    def year_start_rows(self) -> list[tuple[int, int]]:
        """Return ``(year, first_row)`` pairs in display order (newest first)."""

        return [(year, first_row) for year, first_row, _count in self.year_spans()]

    @property
    def generation(self) -> int:
        """Monotonic counter incremented on every data change. Use for cheap change detection."""
        return self._generation

    def prioritize_rows(self, first: int, last: int) -> None:
        if self._direct_mode or self._current_query is None:
            return
        if self._total_count == 0 and self._window_range is None:
            self._load_initial_window()
            if self._total_count == 0:
                return

        first = max(0, first)
        last = max(first, last)
        if self._total_count > 0:
            last = min(last, self._total_count - 1)
        self._visible_range = (first, last)

        # Scrolling into rows that are not cached must reload immediately, but a
        # scan-driven refresh is coalesced: it is triggered by background work,
        # not by the user, so making it wait for the interval keeps the grid from
        # resetting underneath them.
        if self._window_needs_reload(first, last):
            self._reload_window_for_visible_range(first, last, emit_signals=True)
        elif self._pending_scan_refresh:
            self._maybe_flush_pending_scan_refresh()

    def pin_row(self, row: int) -> None:
        if row < 0 or row >= self._total_count:
            self._pinned_row = None
            return
        self._pinned_row = row
        self._ensure_row_loaded(row, emit_signals=True)

    def _geotagged_asset_to_dto(self, asset: object, library_root: Path) -> Optional[AssetDTO]:
        return _geotagged_asset_to_dto_fn(asset, library_root)

    def _scan_row_to_dto(
        self,
        view_root: Path,
        view_rel: str,
        row: dict,
    ) -> Optional[AssetDTO]:
        return _scan_row_to_dto_fn(view_root, view_rel, row)

    def _normalize_abs_key(self, path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        return os.path.normcase(str(resolved))

    def _resolve_abs_path(self, rel_path: Path) -> Path:
        return _resolve_abs_path_fn(rel_path, self._library_root)

    def _path_exists_cached(self, path: Path) -> bool:
        return self._path_cache.exists_cached(path)

    def _should_validate_paths(self, query: AssetQuery) -> bool:
        return _should_validate_paths_fn(query, self._library_root)

    def _is_legacy_thumb_path(self, rel_path: Path) -> bool:
        return _is_legacy_thumb_path_fn(rel_path)

    def _scan_row_is_thumbnail(self, rel: str, row: dict) -> bool:
        return _scan_row_is_thumbnail_fn(rel, row)

    def _should_include_pending(self, pending: _PendingMove, query: AssetQuery) -> bool:
        return _should_include_pending_fn(pending, query)

    def _album_path_for_root(self, root: Path) -> str:
        if self._library_root is None:
            return root.name
        try:
            rel = root.resolve().relative_to(self._library_root.resolve())
        except (OSError, ValueError):
            try:
                rel = root.relative_to(self._library_root)
            except ValueError:
                return root.name
        return rel.as_posix()

    def _rel_path_for_abs(self, path: Path) -> Path:
        if self._library_root is None:
            return Path(path.name)
        try:
            return path.resolve().relative_to(self._library_root.resolve())
        except (OSError, ValueError):
            try:
                return path.relative_to(self._library_root)
            except ValueError:
                return Path(path.name)

    def _slice_query(self, query: AssetQuery, offset: int, limit: int) -> AssetQuery:
        sliced = self._clone_query(query)
        sliced.offset = offset
        sliced.limit = max(0, limit)
        return sliced

    def _count_query(self, query: AssetQuery) -> AssetQuery:
        counted = self._clone_query(query)
        counted.limit = None
        counted.offset = 0
        return counted

    @staticmethod
    def _clone_query(query: AssetQuery) -> AssetQuery:
        return copy.deepcopy(query)

    def _iter_cached_rows(self) -> List[tuple[int, AssetDTO]]:
        return sorted(self._row_cache.items(), key=lambda item: item[0])

    def _count_cache_key(self) -> tuple[str, str, str] | None:
        """Return a stable key for the current query scope.

        Paths are normalised through ``str`` so the key does not depend on the
        ``repr`` of a ``Path``; the query is still snapshotted via ``repr`` because
        ``AssetQuery`` is mutable and deliberately kept that way by the domain layer.
        """

        query = self._current_query
        if query is None:
            return None
        return (str(self._active_root), str(self._library_root), repr(query))

    def _cached_total(self) -> int | None:
        if self._cached_count_key != self._count_cache_key():
            return None
        return self._cached_count

    def _store_total(self, total: int) -> None:
        self._cached_count = int(total)
        self._cached_count_key = self._count_cache_key()

    def invalidate_count_cache(self) -> None:
        """Drop the cached row count.

        The key only changes when the query or the library root changes, so rows that
        appear because of a scan would otherwise leave a stale count behind. Callers
        that know the data changed invalidate explicitly.
        """

        self._cached_count = None
        self._cached_count_key = None

    def reset_visible_window(self) -> None:
        self._generation += 1
        self._row_cache.clear()
        # Do NOT zero the row count here. The model reports ``count()`` as its row
        # count, so zeroing it collapses the scrollbar range to nothing and the view
        # snaps back to the top — that is why a timeline drag "stuck" at positions it
        # had already passed. The next finished load sets the authoritative total; in
        # between, the previous (still valid) count keeps the scrollbar stable.
        self._window_range = None
        self._visible_range = None
        self._pinned_row = None
        self._path_cache.clear()
        self._pending_moves.clear()
        self._pending_paths.clear()
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()
