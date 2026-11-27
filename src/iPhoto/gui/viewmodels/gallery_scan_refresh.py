"""Scan-driven refresh policy for :class:`GalleryCollectionStore`.

A scan emits a chunk for every directory it finishes. Each one used to decide
inline whether it could change the rows on screen, coalescing them so a busy
scan does not reset the gallery model dozens of times per second. That policy
(plus its path mapping and sort-key checks) is independent of paging and row
storage, so it now lives in this mixin. The methods are moved verbatim: the
store keeps the state, this file keeps the decision rules.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from iPhoto.application.dtos import AssetDTO


class GalleryScanRefreshMixin:
    """Coalesce scan chunks into at most one visible-window reload per interval."""

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
        # A scan may have added rows, and the query did not change: drop the cached
        # count so the next load re-reads it.
        self.invalidate_count_cache()
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
        if self._scan_refresh_suppressed:
            # Held back by an active timeline drag; the pending flag survives so the
            # refresh runs once the drag ends.
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
