"""Background worker that performs low-pressure face scanning."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThread, Signal

from ...people.index_coordinator import (
    PeopleIndexCoordinator,
    PeopleSnapshotCommittedError,
)
from ...people.pipeline import FaceClusterPipeline
from ...people.service import PeopleService, face_library_paths
from ...people.status import (
    FACE_STATUS_FAILED,
    FACE_STATUS_PENDING,
    FACE_STATUS_RETRY,
    is_face_scan_candidate,
    normalize_face_status,
)
from ...utils.logging import get_logger

LOGGER = get_logger()


class FaceScanWorker(QThread):
    """Consume pending People assets from the session service."""

    peopleIndexUpdated = Signal()
    statusChanged = Signal(str)
    downloadProgress = Signal(int, int)

    # Keep face scanning responsive to the app: detection still runs in a
    # worker thread, but each committed batch rebuilds the People snapshot and
    # touches SQLite. Very small batches make that bookkeeping dominate UI use.
    # ⭐ 优化：降低批处理大小并增加间隔，确保视频播放流畅
    BATCH_SIZE = 16           # ⭐ 从64降到16，减少单次CPU占用时间
    QUEUE_TARGET_SIZE = 128    # 从64增加到128，匹配更大的批处理大小
    BATCH_IDLE_MS = 1000       # ⭐ 默认1秒间隔

    def __init__(
        self,
        library_root: Path,
        parent=None,
        *,
        people_service: PeopleService | None = None,
    ) -> None:
        super().__init__(parent)
        self._library_root = Path(library_root)
        if people_service is None:
            from ...bootstrap.library_people_service import create_people_service

            people_service = create_people_service(self._library_root)
        self._people_service = people_service
        self._queue: queue.Queue[dict] = queue.Queue()
        self._queued_ids: set[str] = set()
        self._input_closed = False
        self._cancelled = False
        # ⭐ 动态批处理间隔和暂停机制
        self._base_batch_idle_ms = 1000  # ⭐ 默认1秒
        self._current_batch_idle_ms = 1000
        self._paused_for_video = False  # 视频播放时暂停标志

    def enqueue_rows(self, rows: Iterable[dict]) -> None:
        for row in rows:
            asset_id = str(row.get("id") or "")
            status = normalize_face_status(row.get("face_status"))
            if not asset_id or asset_id in self._queued_ids:
                continue
            if status not in {None, FACE_STATUS_RETRY, FACE_STATUS_PENDING}:
                continue
            if not is_face_scan_candidate(row):
                continue
            self._queued_ids.add(asset_id)
            self._queue.put(dict(row))

    def finish_input(self) -> None:
        self._input_closed = True

    def cancel(self) -> None:
        self._cancelled = True
        self._input_closed = True

    def set_video_playing(self, is_playing: bool) -> None:
        """⭐ 动态调整人脸扫描优先级：视频播放时完全暂停处理"""
        if is_playing:
            self._paused_for_video = True  # 暂停处理
            self._current_batch_idle_ms = 3000  # 增加间隔到3秒
            self.statusChanged.emit("人脸扫描已暂停（视频播放中）")
            LOGGER.info("Face scan paused due to video playback")
        else:
            self._paused_for_video = False  # 恢复处理
            self._current_batch_idle_ms = self._base_batch_idle_ms  # 恢复1秒
            self.statusChanged.emit("正在扫描人脸...")
            LOGGER.info("Face scan resumed after video playback")

    def run(self) -> None:  # type: ignore[override]
        try:
            self._run_impl()
        except Exception:
            LOGGER.exception("FaceScanWorker.run crashed")
            try:
                self.statusChanged.emit("Face scanning stopped due to an error.")
            except Exception:
                pass

    def _run_impl(self) -> None:
        # Deferred startup: rows are queued by the file scanner via enqueue_rows().
        # We do NOT call _prime_pending_rows() here because:
        # 1. It would immediately pull ALL pending rows from the DB at once,
        #    causing a huge spike in CPU/memory usage.
        # 2. The file scanner already enqueued all discovered rows during its
        #    scan, so _top_up_pending_rows() below will gradually pull any
        #    remaining rows as the queue drains.
        # 3. This avoids competing with the file scanner for DB and disk I/O.
        if self._cancelled:
            return

        paths = face_library_paths(self._library_root)

        def _on_download_progress(downloaded: int, total: int) -> None:
            self.downloadProgress.emit(downloaded, total)

        pipeline = FaceClusterPipeline(
            model_root=paths.model_dir,
            on_download_progress=_on_download_progress,
        )
        coordinator = self._people_service.coordinator
        if coordinator is None:
            self.statusChanged.emit("Face scanning is unavailable for this library.")
            return

        model_ready = False

        while not self._cancelled:
            try:
                self._top_up_pending_rows()
            except Exception:
                LOGGER.warning("Failed to top up pending rows", exc_info=True)
                if self._cancelled:
                    return
                continue
            batch = self._next_batch()
            if not batch:
                if self._input_closed:
                    try:
                        self._top_up_pending_rows()
                    except Exception:
                        LOGGER.warning("Failed to top up pending rows on drain", exc_info=True)
                    if self._queue.empty():
                        return
                # ⭐ 视频播放时使用更长的等待时间
                wait_ms = 3000 if self._paused_for_video else self._current_batch_idle_ms
                self.msleep(wait_ms)
                continue

            try:
                # ⭐ 视频播放时暂停处理，跳过批处理
                if self._paused_for_video:
                    self.msleep(2000)  # 等待2秒后再检查
                    continue

                if not model_ready:
                    self.statusChanged.emit("正在下载人脸检测模型，首次需要下载约 120MB，请稍候...")
                committed = self._process_batch(
                    batch,
                    coordinator,
                    pipeline,
                    paths.thumbnail_dir,
                )
                if not model_ready:
                    model_ready = True
                    self.statusChanged.emit("正在扫描人脸...")
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                if committed:
                    self.peopleIndexUpdated.emit()
                # Brief pause between batches to reduce CPU/IO pressure and let
                # the UI thread process events.
                self.msleep(self._current_batch_idle_ms)
            except PeopleSnapshotCommittedError as exc:
                LOGGER.error("Face scan bookkeeping failed after commit: %s", exc, exc_info=True)
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                try:
                    self.statusChanged.emit(str(exc))
                except Exception:
                    pass
                return
            except RuntimeError as exc:
                try:
                    self._mark_remaining_failed(batch)
                except Exception:
                    LOGGER.warning("Failed to mark remaining failed", exc_info=True)
                self.statusChanged.emit(str(exc))
                return
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                LOGGER.warning("Face scan batch failed: %s", exc, exc_info=True)
                # The batch is retried so the assets remain pending/retry in the
                # store and will be re-detected on the next scan.  We do NOT
                # extend pending_done_ids here because we cannot guarantee
                # session.commit() will succeed for partially staged results.
                try:
                    self._mark_rows_retry(batch)
                except Exception:
                    LOGGER.warning("Failed to mark rows as retry", exc_info=True)
                reason = str(exc).strip() or exc.__class__.__name__
                self.statusChanged.emit(f"Face scanning paused: {reason}")
                if self._input_closed:
                    return

    def _prime_pending_rows(self) -> None:
        self._top_up_pending_rows()

    def _top_up_pending_rows(self) -> None:
        store = self._people_service.asset_repository
        if store is None:
            return
        attempts = 0
        while self._queue.qsize() < self.QUEUE_TARGET_SIZE and attempts < 3 and not self._cancelled:
            queue_size_before = self._queue.qsize()
            deficit = max(self.QUEUE_TARGET_SIZE - queue_size_before, self.BATCH_SIZE)
            self.enqueue_rows(
                store.read_rows_by_face_status(
                    [FACE_STATUS_PENDING, FACE_STATUS_RETRY],
                    limit=max(deficit * 4, self.BATCH_SIZE),
                )
            )
            attempts += 1
            if self._queue.qsize() == queue_size_before:
                break

    def _next_batch(self) -> list[dict]:
        try:
            first = self._queue.get(timeout=0.25)
        except queue.Empty:
            return []

        batch = [first]
        while len(batch) < self.BATCH_SIZE:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _process_batch(
        self,
        batch: list[dict],
        coordinator: PeopleIndexCoordinator,
        pipeline: FaceClusterPipeline,
        thumbnail_dir: Path,
    ) -> bool:
        """Detect faces for *batch* and commit a realtime People snapshot."""
        if self._cancelled:
            self._mark_rows_retry(batch)
            return False
        detected = list(
            pipeline.detect_faces_for_rows(
                batch,
                library_root=self._library_root,
                thumbnail_dir=thumbnail_dir,
                is_cancelled=lambda: self._cancelled,
            )
        )

        if self._cancelled:
            self._mark_rows_retry(batch)
            return False

        retry_items = [item for item in detected if item.asset_id and item.error]
        if retry_items:
            LOGGER.warning(
                "Face scan: %d asset(s) could not be processed (first: %s)",
                len(retry_items),
                retry_items[0].asset_rel,
            )
        retry_id_set = {str(item.asset_id) for item in retry_items}
        retry_source_ids = {
            str(row.get("id") or "")
            for row in batch
            if str(row.get("id") or "") in retry_id_set
            and normalize_face_status(row.get("face_status")) == FACE_STATUS_RETRY
        }
        first_retry_ids = [asset_id for asset_id in retry_id_set if asset_id not in retry_source_ids]
        failed_ids = [asset_id for asset_id in retry_id_set if asset_id in retry_source_ids]

        if first_retry_ids:
            self.statusChanged.emit("Some assets need a face-scan retry.")
        if failed_ids:
            self._update_face_statuses(
                failed_ids,
                FACE_STATUS_FAILED,
            )
            self.statusChanged.emit(
                "Some assets could not be face scanned and will be retried after a rescan."
            )
        retry_detected = [
            item
            for item in detected
            if not item.asset_id or str(item.asset_id) not in failed_ids
        ]

        event = coordinator.submit_detected_batch(
            retry_detected,
            distance_threshold=pipeline.distance_threshold,
            min_samples=pipeline.min_samples,
        )
        return event is not None

    def _mark_rows_retry(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        self._update_face_statuses(ids, FACE_STATUS_RETRY)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_remaining_retry(self, initial_rows: Iterable[dict]) -> None:
        self._mark_rows_retry(initial_rows)
        remaining = list(
            self._read_rows_by_face_status([FACE_STATUS_PENDING, FACE_STATUS_RETRY])
        )
        self._mark_rows_retry(remaining)

    def _mark_rows_failed(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        self._update_face_statuses(ids, FACE_STATUS_FAILED)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_remaining_failed(self, initial_rows: Iterable[dict]) -> None:
        self._mark_rows_failed(initial_rows)
        remaining = list(self._read_rows_by_face_status([FACE_STATUS_PENDING, FACE_STATUS_RETRY]))
        self._mark_rows_failed(remaining)

    def _read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterable[dict]:
        store = self._people_service.asset_repository
        if store is None:
            return ()
        return store.read_rows_by_face_status(statuses, limit=limit)

    def _update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        store = self._people_service.asset_repository
        if store is None:
            return
        try:
            store.update_face_statuses(asset_ids, status)
        except Exception:
            LOGGER.warning("Failed to update face statuses to %s", status, exc_info=True)
