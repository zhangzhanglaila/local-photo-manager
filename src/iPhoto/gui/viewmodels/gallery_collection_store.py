"""Pure Python collection store backing gallery and filmstrip selection."""

from __future__ import annotations

import copy
import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from PySide6.QtCore import QThreadPool

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.signal import Signal

from iPhoto.gui.viewmodels.gallery_load_worker import (
    GalleryLoadSignals,
    GalleryLoadWorker,
)

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


class GalleryCollectionStore:
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

        self._asset_query_service = asset_query_service
        self._library_root = library_root or getattr(asset_query_service, "library_root", None)
        self._current_query: Optional[AssetQuery] = None
        self._selection_query: Optional[AssetQuery] = None
        self._selection_direct_assets: Optional[list] = None
        self._selection_library_root: Optional[Path] = library_root
        self._total_count = 0
        self._generation = 0
        self._row_cache: Dict[int, AssetDTO] = {}
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
        self._reset_window_state()
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
        self._reset_window_state()
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
        logging.getLogger(__name__).info(
            "[DIAG] _load_query: active_root=%s library_root=%s service=%s query=%s",
            self._active_root, self._library_root,
            self._asset_query_service is not None, query,
        )
        self._reset_window_state()
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
        self._reset_window_state()

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
        cache_key: tuple = (
            ("direct", self._generation, self._total_count)
            if query is None
            else (id(query), self._total_count)
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

        result: list[tuple[int, int, int]] = []
        row = 0
        current_year: int | None = None
        year_count = 0
        for year, _month, count in buckets:
            if year != current_year:
                if current_year is not None:
                    result.append((current_year, first_row, year_count))
                current_year = year
                year_count = 0
                first_row = min(row, max(0, self._total_count - 1))
            increment = max(0, count)
            year_count += increment
            row += increment
        if current_year is not None:
            result.append((current_year, first_row, year_count))
        return result

    def year_start_rows(self) -> list[tuple[int, int]]:
        """Return ``(year, first_row)`` pairs in display order (newest first)."""

        return [(year, first_row) for year, first_row, _count in self.year_spans()]

    @property
    def generation(self) -> int:
        """Monotonic counter incremented on every data change. Use for cheap change detection."""
        return self._generation

    def update_favorite_status(self, row: int, is_favorite: bool) -> None:
        dto = self._row_cache.get(row)
        if dto is None and row >= 0:
            dto = self.asset_at(row)
        if dto is None:
            return
        dto.is_favorite = is_favorite
        self.row_changed.emit(row)

    def update_asset_metadata(self, row: int, metadata: Dict[str, object]) -> None:
        dto = self._row_cache.get(row)
        if dto is None and row >= 0:
            dto = self.asset_at(row)
        if dto is None:
            return
        dto.metadata = dict(metadata)
        width = metadata.get("w")
        height = metadata.get("h")
        duration = metadata.get("dur")
        size_bytes = metadata.get("bytes")
        if isinstance(width, int) and width > 0:
            dto.width = width
        if isinstance(height, int) and height > 0:
            dto.height = height
        if isinstance(duration, (int, float)) and float(duration) >= 0.0:
            dto.duration = float(duration)
        if isinstance(size_bytes, int) and size_bytes >= 0:
            dto.size_bytes = size_bytes
        self.row_changed.emit(row)

    def remove_rows(self, rows: List[int], *, emit: bool = True) -> None:
        if not rows:
            return
        removed = sorted({row for row in rows if 0 <= row < self._total_count})
        if not removed:
            return

        old_total = self._total_count
        removed_set = set(removed)
        new_cache: Dict[int, AssetDTO] = {}
        removed_before = 0
        removed_index = 0
        removed_count = len(removed)
        for row in sorted(self._row_cache):
            while removed_index < removed_count and removed[removed_index] < row:
                removed_before += 1
                removed_index += 1
            if row in removed_set:
                continue
            new_cache[row - removed_before] = self._row_cache[row]

        self._row_cache = new_cache
        self._total_count = max(0, old_total - len(removed))
        self._window_range = None
        self._visible_range = None if self._total_count == 0 else self._visible_range

        if self._pinned_row is not None:
            if self._pinned_row in removed_set:
                self._pinned_row = None
            else:
                shift = sum(1 for row in removed if row < self._pinned_row)
                self._pinned_row -= shift

        if emit:
            self.count_changed.emit(old_total, self._total_count)
            if self._total_count > 0:
                self.window_changed.emit(max(0, removed[0] - 1), self._total_count - 1)
            self.data_changed.emit()

    def apply_optimistic_move(
        self,
        paths: List[Path],
        destination_root: Path,
        *,
        is_delete: bool,
    ) -> tuple[list[int], list[AssetDTO]]:
        if not paths:
            return [], []
        destination_album_path = self._album_path_for_root(destination_root)
        removed_rows: list[int] = []
        inserted_dtos: list[AssetDTO] = []
        cached_map = {
            str(dto.abs_path): (idx, dto) for idx, dto in self._iter_cached_rows()
        }
        for path in paths:
            key = str(path)
            if key in self._pending_paths:
                continue
            found = cached_map.get(key)
            if found is None:
                continue
            row, dto = found
            removed_rows.append(row)
            destination_abs = destination_root / path.name
            destination_rel = self._rel_path_for_abs(destination_abs)
            moved_dto = AssetDTO(
                id=dto.id,
                abs_path=destination_abs,
                rel_path=destination_rel,
                media_type=dto.media_type,
                created_at=dto.created_at,
                width=dto.width,
                height=dto.height,
                duration=dto.duration,
                size_bytes=dto.size_bytes,
                metadata=dto.metadata,
                is_favorite=dto.is_favorite,
                is_live=dto.is_live,
                is_pano=dto.is_pano,
                micro_thumbnail=dto.micro_thumbnail,
            )
            pending = _PendingMove(
                dto=moved_dto,
                source_abs=path,
                destination_root=destination_root,
                destination_album_path=destination_album_path,
                destination_abs=destination_abs,
                destination_rel=destination_rel,
                is_delete=is_delete,
            )
            self._pending_moves.append(pending)
            self._pending_paths.add(key)
            if self._current_query and self._should_include_pending(pending, self._current_query):
                inserted_dtos.append(moved_dto)
        return removed_rows, inserted_dtos

    def append_dtos(self, dtos: List[AssetDTO]) -> None:
        if not dtos:
            return
        start = self._total_count
        for offset, dto in enumerate(dtos):
            self._row_cache[start + offset] = dto
        old_total = self._total_count
        self._total_count += len(dtos)
        self.count_changed.emit(old_total, self._total_count)
        self.data_changed.emit()

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

    def handle_scan_chunk(self, scan_root: Path, chunk: List[dict]) -> None:
        if not chunk:
            return
        # ⭐ 关键修复：在第一次加载完成前，不响应 rescan chunk
        # 避免 rescan 的 handle_scan_chunk 与 _load_query 时序冲突，
        # 导致 All Photos 加载时被打断、row_cache 被重置为 0 行
        if self._load_worker is not None:
            # 正在初次加载，忽略 scan chunk
            return
        # ⭐ 修复：即使没有活动根目录，也记录扫描到的路径以便后续加载
        # 这允许在初始化查询时也能显示扫描到的照片
        if self._active_root is None:
            # 仍然尝试映射 - 这允许后续 set_active_root 触发加载
            if self._library_root is not None:
                try:
                    scan_root_resolved = scan_root.resolve()
                    view_root_resolved = self._library_root.resolve()
                except OSError:
                    return
                # 兼容直接匹配或父子关系
                is_compatible = (
                    scan_root_resolved == view_root_resolved
                    or scan_root_resolved in view_root_resolved.parents
                    or view_root_resolved in scan_root_resolved.parents
                )
                if is_compatible:
                    # 暂存 chunk 中的 rel 路径
                    self._pending_scan_rels.update(
                        str(row.get("rel", "")) for row in chunk
                        if isinstance(row, dict) and isinstance(row.get("rel"), str)
                    )
                    self._pending_scan_sort_keys.update(
                        (row.get("dt", ""), str(row.get("id", "")))
                        for row in chunk
                        if isinstance(row, dict)
                        and isinstance(row.get("dt"), str)
                        and row.get("dt")
                    )
                    self._pending_scan_refresh = True
            return

        mapped_entries = self._map_scan_rows_to_active_entries(scan_root, chunk)
        if not mapped_entries:
            return

        self._pending_scan_rels.update(view_rel for view_rel, _row in mapped_entries)
        self._pending_scan_sort_keys.update(
            sort_key
            for _view_rel, row in mapped_entries
            for sort_key in [self._sort_key_from_scan_row(row)]
            if sort_key is not None
        )
        self._pending_scan_refresh = True

        if self._visible_range is None:
            # Gallery was empty on first load — reload the initial window so
            # newly scanned rows appear without requiring the user to navigate
            # away and back.
            # ⭐ 如果没有查询，先创建默认查询
            if self._current_query is None and self._asset_query_service is not None:
                from iPhoto.domain.models.query import AssetQuery
                self._current_query = AssetQuery()
            if self._current_query is not None:
                self._load_initial_window()
            return

        visible_first, visible_last = self._visible_range
        if self._scan_chunk_affects_visible_window(visible_first, visible_last):
            self._maybe_flush_pending_scan_refresh()

    def handle_scan_finished(self, root: Path, success: bool) -> None:
        if not success:
            return
        # ⭐ 关键修复：在第一次加载完成前，不响应 scan_finished
        # 避免 rescan 完成后立即触发 reload，打断正在进行的初次加载
        if self._load_worker is not None:
            # 正在初次加载，等加载完成后再处理
            return
        # ⭐ 修复：即使查询为空也尝试加载，以便在扫描完成后显示照片
        # 这处理了"All Photos"视图在扫描过程中逐渐加载的情况
        if not self._scan_root_matches_active_root(root):
            # 不匹配当前活动的根目录，忽略
            return
        self._pending_scan_refresh = True
        if self._visible_range is not None:
            # Final refresh of a scan: force it so no change stays pending.
            self._maybe_flush_pending_scan_refresh(force=True)
        else:
            # Gallery was empty — try loading now that scan data exists.
            self._load_initial_window()

    def _clear_pending_scan_refresh(self) -> None:
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()

    def _maybe_flush_pending_scan_refresh(self, *, force: bool = False) -> None:
        """Apply accumulated scan changes to the visible window, at most once per interval.

        Two things used to make this run away. The pending flag was never cleared
        after a reload, so it stayed true for the rest of the session and every
        ``prioritize_rows`` call — which fires on each repaint — reloaded again;
        a completed scan therefore kept the gallery resetting indefinitely. And
        each scan chunk reloaded immediately, so a busy scan reset the model
        dozens of times per second.

        Consuming the flag bounds reloads to actual new data, and the interval
        keeps a rapid chunk stream from resetting the grid on every chunk. The
        final refresh after a scan uses ``force=True`` so nothing is left pending.
        """

        if not self._pending_scan_refresh:
            return
        if not force:
            if time.monotonic() - self._last_scan_refresh_at < self.SCAN_REFRESH_MIN_INTERVAL_S:
                return
        # ⭐ 如果查询为空但有活动根目录，创建默认查询
        if self._current_query is None and self._active_root is not None and self._asset_query_service is not None:
            from iPhoto.domain.models.query import AssetQuery
            self._current_query = AssetQuery()

        if self._current_query is None:
            self._clear_pending_scan_refresh()
            return
        if self._visible_range is None:
            self._clear_pending_scan_refresh()
            return
        first, last = self._visible_range
        if not self._scan_chunk_affects_visible_window(first, last):
            return
        self._last_scan_refresh_at = time.monotonic()
        self._clear_pending_scan_refresh()
        self._reload_window_for_visible_range(first, last, emit_signals=True)

    def _load_initial_window(
        self, visible_range: tuple[int, int] | None = None
    ) -> None:
        """Load the first window, or a specific visible range when restoring.

        ``visible_range`` matters when a load was deferred until the query service
        became available: the gallery used to fall back to rows ``0..N`` and forget
        where the user actually was.
        """

        if visible_range is None:
            first, last = 0, self.INITIAL_VISIBLE_ROWS - 1
        else:
            first, last = visible_range
            first = max(0, first)
            last = max(first, last)
        self._visible_range = (first, last)
        self._reload_window_for_visible_range(first, last, emit_signals=True)

    def _window_bounds_to_load(self, first: int, last: int) -> tuple[int, int]:
        """Expand the requested visible range to the configured cache window.

        Loading exactly the visible rows leaves no slack: every scroll step past
        them forces a reload, and each reload resets the gallery model, so
        scrolling felt like it was fighting the view. ``_compute_target_window``
        already describes the intended window (look-behind + viewport +
        look-ahead, clamped to ``MIN/MAX_WINDOW_SIZE``) but was never called.
        """

        total = self._total_count
        if total <= 0:
            # The collection size is not known before the first load. Ask for a
            # full window anyway so the first result already covers scrolling.
            total = max(0, last) + 1 + self.MIN_WINDOW_SIZE
        return self._compute_target_window(max(0, first), max(first, last), total)

    def _reload_window_for_visible_range(self, first: int, last: int, *, emit_signals: bool) -> None:
        # ⭐ 如果查询为空但有活动根目录，尝试加载"All Photos"
        if self._current_query is None:
            if self._active_root is not None and self._asset_query_service is not None:
                # 创建一个默认查询来加载所有照片
                from iPhoto.domain.models.query import AssetQuery
                default_query = AssetQuery()
                self._current_query = default_query
            else:
                if self._asset_query_service is None:
                    old_total = self._total_count
                    self._reset_window_state()
                    if emit_signals and old_total != 0:
                        self.count_changed.emit(old_total, 0)
                        self.data_changed.emit()
                    # ⭐ 如果 _active_root 已设置但服务还没绑定，等待服务绑定后再加载
                    if self._active_root is not None:
                        self._pending_load_after_service_bind = True
                        self._pending_load_range = (max(0, first), max(first, last))
                    return

        # ⭐ 如果服务还没绑定但有 root，等待绑定
        if self._asset_query_service is None:
            if self._active_root is not None:
                self._pending_load_after_service_bind = True
                self._pending_load_range = (first, last)
            return

        # Cancel any in-flight load.
        if self._load_worker is not None:
            self._load_worker.cancel()
            self._load_worker = None

        self._load_generation += 1
        gen = self._load_generation
        load_first, load_last = self._window_bounds_to_load(first, last)
        self._pending_load_range = (load_first, load_last)
        import logging
        logging.getLogger(__name__).info(
            "[DIAG] _reload_window_for_visible_range: starting worker gen=%s visible=(%s,%s) load=(%s,%s) total=%s active_root=%s service=%s",
            gen, first, last, load_first, load_last, self._total_count, self._active_root,
            self._asset_query_service is not None,
        )

        # Lazily create a single signals object reused across loads.
        if self._load_signals is None:
            self._load_signals = GalleryLoadSignals()
            self._load_signals.finished.connect(self._on_load_finished)
            self._load_signals.error.connect(self._on_load_error)

        validate = self._should_validate_paths(self._current_query)
        worker = GalleryLoadWorker(
            generation=gen,
            asset_query_service=self._asset_query_service,
            active_root=self._active_root or self._library_root,
            query=self._current_query,
            first=load_first,
            last=load_last,
            signals=self._load_signals,
            validate_paths=validate,
        )
        self._load_worker = worker
        self._emit_signals_on_load = emit_signals
        QThreadPool.globalInstance().start(worker)

    def _on_load_finished(self, generation: int, row_cache: dict, total: int) -> None:
        """Handle async load completion on the main thread."""
        import logging
        logging.getLogger(__name__).info(
            "[DIAG] _on_load_finished: gen=%s (expected=%s) row_cache=%d total=%d active_root=%s library_root=%s has_query=%s",
            generation, self._load_generation, len(row_cache), total,
            self._active_root, self._library_root, self._current_query is not None,
        )
        if generation != self._load_generation:
            logging.getLogger(__name__).info(
                "[DIAG] _on_load_finished: STALE (gen mismatch), discarding"
            )
            return  # stale result, discard
        self._load_worker = None

        old_total = self._total_count
        self._row_cache = row_cache
        self._total_count = total

        if total <= 0:
            self._reset_window_state()
            self._total_count = 0
            if self._emit_signals_on_load and old_total != 0:
                self.count_changed.emit(old_total, 0)
                self.data_changed.emit()
            return

        # Preserve pinned row if it was in the old cache but not in new results.
        pinned_row = self._pinned_row
        if pinned_row is not None and pinned_row not in row_cache and 0 <= pinned_row < total:
            try:
                pinned_dto = self._fetch_single_row(pinned_row)
            except Exception:
                logging.getLogger(__name__).exception(
                    "_fetch_single_row failed for pinned row %s", pinned_row
                )
                pinned_dto = None
            if pinned_dto is not None:
                row_cache[pinned_row] = pinned_dto

        # ⭐ 修复：设置 _window_range，否则 _window_needs_reload 一直返回 True
        # 导致 prioritize_rows 反复触发 _reload_window_for_visible_range，
        # 进而触发 modelReset，gallery view 一直显示"加载中"
        if row_cache:
            loaded_first = min(row_cache.keys())
            loaded_last = max(row_cache.keys())
            self._window_range = (loaded_first, loaded_last)
            if self._visible_range is None:
                self._visible_range = (loaded_first, loaded_last)
        elif total > 0:
            # total > 0 但 row_cache 为空（所有 row 都被过滤），
            # 也要设置 _window_range 以避免无限重载循环
            self._window_range = (0, total - 1)
            if self._visible_range is None:
                self._visible_range = (0, total - 1)
        else:
            self._window_range = None

        self._generation += 1
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()

        if self._emit_signals_on_load:
            if old_total != total:
                self.count_changed.emit(old_total, total)
            self.data_changed.emit()

    def _on_load_error(self, generation: int, error_msg: str) -> None:
        """Handle async load error on the main thread."""
        if generation != self._load_generation:
            return
        self._load_worker = None
        self._logger.error("Gallery load failed: %s", error_msg)

    def _compute_target_window(self, first: int, last: int, total_count: int) -> tuple[int, int]:
        visible_count = max(1, last - first + 1)
        target_size = min(
            self.MAX_WINDOW_SIZE,
            max(self.MIN_WINDOW_SIZE, visible_count * self.WINDOW_MULTIPLIER),
        )
        lookbehind = visible_count * self.LOOKBEHIND_SCREENS
        lookahead = visible_count * self.LOOKAHEAD_SCREENS

        window_first = max(0, first - lookbehind)
        window_last = min(total_count - 1, last + lookahead)
        current_size = max(0, window_last - window_first + 1)
        deficit = max(0, target_size - current_size)
        if deficit > 0:
            extend_ahead = min(total_count - 1 - window_last, deficit)
            window_last += extend_ahead
            deficit -= extend_ahead
            if deficit > 0:
                window_first = max(0, window_first - deficit)
        return window_first, window_last

    def _window_needs_reload(self, first: int, last: int) -> bool:
        if self._window_range is None:
            return True

        window_first, window_last = self._window_range
        if first < window_first or last > window_last:
            return True

        window_size = max(1, window_last - window_first + 1)
        margin = max(1, int(window_size * self.HYSTERESIS_RATIO))
        # The keep-zone margin only makes sense when the cached window actually
        # extends past the visible range, i.e. when there is slack to consume.
        # Windows are loaded for exactly the requested range, so slack is
        # normally zero: requiring it unconditionally made every call report
        # "needs reload", each reload rebuilt the same window, reset the model,
        # and the reset's visible-rows change asked again — a busy loop that
        # pinned the CPU.
        if window_first > 0 and first > window_first and first - window_first <= margin:
            return True
        if (
            self._total_count > 0
            and window_last < self._total_count - 1
            and last < window_last
            and window_last - last <= margin
        ):
            return True

        for row in range(first, last + 1):
            if row == self._pinned_row:
                continue
            if row not in self._row_cache:
                return True
        return False

    def _fetch_rows(self, first: int, last: int) -> Dict[int, AssetDTO]:
        if self._current_query is None or last < first:
            return {}

        query = self._slice_query(self._current_query, first, last - first + 1)
        validate_paths = self._should_validate_paths(self._current_query)
        rows: Dict[int, AssetDTO] = {}
        active_root = self._active_root or self._library_root
        if active_root is None or self._asset_query_service is None:
            return {}
        for offset, row in enumerate(
            self._asset_query_service.read_query_asset_rows(active_root, query)
        ):
            row_index = first + offset
            view_rel = row.get("rel") if isinstance(row, dict) else None
            if not isinstance(view_rel, str) or not view_rel:
                continue
            if self._scan_row_is_thumbnail(view_rel, row):
                continue
            dto = self._scan_row_to_dto(active_root, view_rel, row)
            if dto is None:
                continue
            if validate_paths and not self._path_exists_cached(dto.abs_path):
                continue
            rows[row_index] = dto
        return rows

    def _fetch_single_row(self, row: int) -> Optional[AssetDTO]:
        if self._current_query is None or row < 0:
            return None
        fetched = self._fetch_rows(row, row)
        return fetched.get(row)

    def _ensure_row_loaded(self, row: int, *, emit_signals: bool) -> None:
        if row in self._row_cache:
            return
        try:
            dto = self._fetch_single_row(row)
        except Exception:
            logging.getLogger(__name__).exception(
                "_fetch_single_row failed for row %s", row
            )
            return
        if dto is None:
            return
        self._row_cache[row] = dto
        if emit_signals:
            self.window_changed.emit(row, row)

    def preload_rows_around(self, center: int, radius: int = 10) -> None:
        """Preload rows in [center-radius, center+radius] into the cache.

        Loads uncached rows one at a time via ``QTimer.singleShot`` so the
        GUI thread is never blocked for more than one DB query per tick.
        """

        first = max(0, center - radius)
        last = min(self._total_count - 1, center + radius)
        if first > last:
            return
        uncached = [r for r in range(first, last + 1) if r not in self._row_cache]
        if not uncached:
            return
        self._preload_queue = uncached
        self._preload_next()

    def _preload_next(self) -> None:
        queue = getattr(self, "_preload_queue", None)
        if not queue:
            return
        row = queue.pop(0)
        loaded = False
        if row not in self._row_cache:
            try:
                dto = self._fetch_single_row(row)
            except Exception:
                dto = None
            if dto is not None:
                self._row_cache[row] = dto
                loaded = True
        if loaded:
            # Repaint the row now that its data is available; paint paths only
            # read the cache, so nothing else would refresh it.
            self.row_changed.emit(row)
        if queue:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._preload_next)
        else:
            self._preload_queue = None

    def _map_scan_rows_to_active_entries(self, scan_root: Path, chunk: List[dict]) -> list[tuple[str, dict]]:
        if self._active_root is None:
            return []
        try:
            scan_root_resolved = scan_root.resolve()
            view_root_resolved = self._active_root.resolve()
        except OSError:
            return []

        is_direct_match = scan_root_resolved == view_root_resolved
        is_scan_parent = scan_root_resolved in view_root_resolved.parents
        is_scan_child = view_root_resolved in scan_root_resolved.parents
        is_non_primary_root = not (is_direct_match or is_scan_parent or is_scan_child)

        mapped: list[tuple[str, dict]] = []
        for row in chunk:
            raw_rel = row.get("rel")
            if not isinstance(raw_rel, str) or not raw_rel:
                continue
            if is_non_primary_root:
                # Non-primary root assets store absolute paths in rel — pass through as-is.
                mapped.append((raw_rel, row))
                continue
            view_rel = self._resolve_view_rel(
                raw_rel,
                scan_root_resolved,
                view_root_resolved,
                is_scan_parent=is_scan_parent,
                is_scan_child=is_scan_child,
            )
            if view_rel:
                mapped.append((view_rel, row))
        return mapped

    def _pending_scan_affects_visible_window(self, first: int, last: int) -> bool:
        return self._window_rel_intersects_pending_scan(first, last) or self._scan_sort_keys_affect_visible_window(first, last)

    def _scan_chunk_affects_visible_window(self, first: int, last: int) -> bool:
        """Return whether newly scanned rows can change what is on screen.

        Reloading the window resets the gallery model, which is expensive and was
        also observed to crash the view when it happened repeatedly while a
        preview was open. So a scan chunk should only cost a reload when it can
        actually change the visible rows: it re-scanned one of them, it sorts
        into their range, or the window is still filling (first scan) and the
        incoming rows may fill the gaps.
        """

        if self._pending_scan_affects_visible_window(first, last):
            return True
        for row in range(first, last + 1):
            if row not in self._row_cache:
                # The window is still incomplete, e.g. the very first scan.
                return True
        return False

    def _window_rel_intersects_pending_scan(self, first: int, last: int) -> bool:
        if not self._pending_scan_rels:
            return False
        for row in range(first, last + 1):
            dto = self._row_cache.get(row)
            if dto is None:
                continue
            if dto.rel_path.as_posix() in self._pending_scan_rels:
                return True
        return False

    def _scan_sort_keys_affect_visible_window(self, first: int, last: int) -> bool:
        if not self._pending_scan_sort_keys:
            return False

        bounds = self._visible_window_sort_bounds(first, last)
        if bounds is None:
            return False

        top_key, bottom_key = bounds
        for key in self._pending_scan_sort_keys:
            if top_key >= key >= bottom_key:
                return True
        return False

    def _visible_window_sort_bounds(self, first: int, last: int) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
        top_key: Optional[tuple[str, str]] = None
        bottom_key: Optional[tuple[str, str]] = None
        for row in range(first, last + 1):
            dto = self._row_cache.get(row)
            if dto is None:
                continue
            sort_key = self._sort_key_from_dto(dto)
            if sort_key is None:
                continue
            if top_key is None:
                top_key = sort_key
            bottom_key = sort_key
        if top_key is None or bottom_key is None:
            return None
        return top_key, bottom_key

    def _sort_key_from_dto(self, dto: AssetDTO) -> Optional[tuple[str, str]]:
        if dto.created_at is None:
            return None
        return dto.created_at.isoformat(), str(dto.id or "")

    def _sort_key_from_scan_row(self, row: dict) -> Optional[tuple[str, str]]:
        dt_raw = row.get("dt")
        if not isinstance(dt_raw, str) or not dt_raw:
            return None
        return dt_raw, str(row.get("id") or "")

    def _scan_root_matches_active_root(self, scan_root: Path) -> bool:
        if self._active_root is None:
            return False
        try:
            scan_root_resolved = scan_root.resolve()
            view_root_resolved = self._active_root.resolve()
        except OSError:
            return False
        if (
            scan_root_resolved == view_root_resolved
            or scan_root_resolved in view_root_resolved.parents
            or view_root_resolved in scan_root_resolved.parents
        ):
            return True
        # In a multi-root library with no album_path filter (All Photos),
        # any root's scan should trigger a gallery refresh.
        if self._current_query is not None and self._current_query.album_path is None:
            return True
        return False

    def _resolve_view_rel(
        self,
        raw_rel: str,
        scan_root: Path,
        view_root: Path,
        *,
        is_scan_parent: bool,
        is_scan_child: bool,
    ) -> Optional[str]:
        if scan_root == view_root:
            return raw_rel
        if is_scan_parent:
            full_path = scan_root / raw_rel
            try:
                return full_path.relative_to(view_root).as_posix()
            except (OSError, ValueError):
                return None
        if is_scan_child:
            try:
                prefix = scan_root.relative_to(view_root).as_posix()
            except ValueError:
                return None
            prefix_slash = f"{prefix}/" if prefix else ""
            return f"{prefix_slash}{raw_rel}" if prefix_slash else raw_rel
        return None

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

    def _reset_window_state(self) -> None:
        self._generation += 1
        self._row_cache.clear()
        self._total_count = 0
        self._window_range = None
        self._visible_range = None
        self._pinned_row = None
        self._path_cache.clear()
        self._pending_moves.clear()
        self._pending_paths.clear()
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()
