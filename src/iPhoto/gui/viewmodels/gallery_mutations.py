"""In-place row mutations for :class:`GalleryCollectionStore`.

Favourite flips, metadata refreshes, removals and optimistic moves all rewrite
the cached rows and have to keep the signals, pinned row and pending-move buffer
in sync. That bookkeeping is independent of paging and scanning, so it lives in
this mixin; the store still owns the state. Methods are moved verbatim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.viewmodels.pending_move_buffer import _PendingMove


class GalleryMutationMixin:
    """Apply local row mutations without reloading the window."""

    def update_favorite_status(self, row: int, is_favorite: bool) -> None:
        dto = self._row_cache.get(row)
        if dto is None and row >= 0:
            dto = self.asset_at(row)
        if dto is None:
            return
        dto.is_favorite = is_favorite

        query = self._current_query
        if query is not None and query.is_favorite and not is_favorite:
            # The row no longer matches the active "favourites" filter. Leaving it in
            # place produced a black tile: the model still reported the row while the
            # query no longer returned it. Removing it keeps model and query in sync.
            self.invalidate_count_cache()
            self.remove_rows([row], emit=True)
            return

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
