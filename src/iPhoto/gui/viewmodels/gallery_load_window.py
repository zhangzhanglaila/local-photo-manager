"""Async window loading for :class:`GalleryCollectionStore`.

The store owns the rows and the viewport; this mixin owns the machinery that
turns a requested row range into a background query and folds the result back
into the cache: worker creation, generation/supersede handling, apply
throttling, target-window maths, row prefetch and the fallback single-row
fetch. Keeping it separate leaves the store with selection and row storage;
the code below is moved verbatim so load timing stays identical.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QThreadPool, QTimer

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.viewmodels.gallery_load_worker import (
    GalleryLoadSignals,
    GalleryLoadWorker,
)
from ...utils.logging import diag


#: Dedicated pool for gallery window loads. Thumbnail decoding, previews and file
#: operations all use `QThreadPool.globalInstance()`; sharing it meant a window load
#: could sit behind a burst of thumbnail jobs for seconds even though the query itself
#: takes ~40 ms. Two threads are enough: loads are small and the newest request wins.
_GALLERY_LOAD_POOL = QThreadPool()
_GALLERY_LOAD_POOL.setMaxThreadCount(2)


class GalleryLoadWindowMixin:
    """Load viewport windows asynchronously and apply them without losing position."""

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
                    self.reset_visible_window()
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

        # Cancel the in-flight load: the newest visible range wins.
        if self._load_worker is not None:
            self._load_worker.cancel()
            self._load_worker = None
            self._load_superseded += 1

        self._load_generation += 1
        gen = self._load_generation
        load_first, load_last = self._window_bounds_to_load(first, last)
        self._pending_load_range = (load_first, load_last)
        import logging
        diag(
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
            known_total=self._cached_total(),
        )
        self._load_worker = worker
        self._load_window_range = (load_first, load_last)
        self._load_requested_at = time.monotonic()
        self._emit_signals_on_load = emit_signals
        _GALLERY_LOAD_POOL.start(worker)

    def set_merge_on_apply(self, merge: bool) -> None:
        """Merge finished loads instead of resetting (see ``_on_load_finished``)."""

        self._merge_on_apply = bool(merge)

    def set_scan_refresh_suppressed(self, suppressed: bool) -> None:
        """Hold back scan-driven refreshes (used while the timeline is dragged)."""

        self._scan_refresh_suppressed = bool(suppressed)
        if self._scan_refresh_suppressed:
            return
        if not self._pending_scan_refresh:
            return
        # Deferred, not immediate: flushing right after the release reset the model
        # while the freshly decoded thumbnails were on screen, which made them vanish
        # and be requested again — the jump looked far slower than it was.
        QTimer.singleShot(300, self._flush_scan_refresh_after_scrub)

    def _flush_scan_refresh_after_scrub(self) -> None:
        if self._scan_refresh_suppressed:
            return
        self._maybe_flush_pending_scan_refresh(force=True)

    def set_apply_throttle(self, seconds: float) -> None:
        """Limit how often a finished load is applied (0 disables the limit)."""

        self._apply_throttle_s = max(0.0, float(seconds))
        if self._apply_throttle_s == 0.0:
            self._last_apply_at = 0.0

    def flush_deferred_load(self) -> None:
        """Apply the newest deferred load immediately (used on release)."""

        deferred, self._deferred_result = self._deferred_result, None
        self._last_apply_at = 0.0
        if deferred is not None:
            self._on_load_finished(*deferred)

    def _on_load_finished(self, generation: int, row_cache: dict, total: int) -> None:
        """Handle async load completion on the main thread."""
        import logging
        if self._apply_throttle_s > 0.0:
            now = time.monotonic()
            if now - self._last_apply_at < self._apply_throttle_s:
                self._deferred_result = (generation, row_cache, total)
                return
            self._last_apply_at = now
        self._deferred_result = None
        _handler_started = time.monotonic()
        diag(
            "[DIAG] _on_load_finished: gen=%s (expected=%s) row_cache=%d total=%d active_root=%s library_root=%s has_query=%s",
            generation, self._load_generation, len(row_cache), total,
            self._active_root, self._library_root, self._current_query is not None,
        )
        elapsed = (
            (time.monotonic() - self._load_requested_at) * 1000
            if self._load_requested_at is not None
            else -1.0
        )
        diag(
            "[DIAG] load finished: gen=%s rows=%d end_to_end=%.0f ms superseded=%d",
            generation, len(row_cache), elapsed, self._load_superseded,
        )
        self._load_superseded = 0
        if generation != self._load_generation:
            diag(
                "[DIAG] _on_load_finished: STALE (gen mismatch), discarding"
            )
            return  # stale result, discard
        self._load_worker = None

        old_total = self._total_count
        window = self._load_window_range
        if (
            self._merge_on_apply
            and self._row_cache
            and row_cache
            and window is not None
            and total == old_total
        ):
            # During a timeline drag the model must keep its rows and scroll offset:
            # a reset snapped the scrollbar back, which made the scrubber dot lag
            # behind the pointer. Folding the window in and refreshing just that range
            # keeps the position and repaints only the new cells.
            self._row_cache.update(row_cache)
            self._store_total(total)
            # Notify only the rows the viewport actually shows. Announcing the whole
            # loaded window made the delegate request decorations (thumbnails) for all
            # ~200 rows on every drag step, so the couple of dozen visible tiles queued
            # behind hundreds of useless decodes and stayed black.
            first, last = window
            visible = self._visible_range
            if visible is not None:
                first = max(first, visible[0])
                last = min(last, visible[1])
            if last >= first:
                self.rows_loaded.emit(first, last)
            return

        self._row_mapping_key = self._count_cache_key()
        self._row_cache = row_cache
        self._total_count = total
        self._store_total(total)

        if total <= 0:
            self.reset_visible_window()
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
        elapsed = (
            (time.monotonic() - self._load_requested_at) * 1000
            if self._load_requested_at is not None
            else -1.0
        )
        diag(
            "[DIAG] load failed: gen=%s end_to_end=%.0f ms superseded=%d error=%s",
            generation, elapsed, self._load_superseded, error_msg,
        )
        self._load_superseded = 0
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
