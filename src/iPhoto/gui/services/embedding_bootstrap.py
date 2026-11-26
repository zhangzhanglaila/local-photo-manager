"""Background workers that download the search model and build its index.

These three workers used to be classes nested inside ``MainCoordinator`` methods.
A nested class gets its own ``self``, so every read of the coordinator's state had
to go through a captured ``coordinator`` alias — the arrangement that produced the
``AttributeError`` that broke search. They now take exactly what they need as
constructor arguments, which also lets the tests build them directly.

Nothing here is on the gallery hot path: they run when a search model must be
fetched or when the semantic index is (re)built.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QRunnable, QThreadPool, Slot

from iPhoto.i18n import tr

_LOGGER = logging.getLogger(__name__)


class SearchModelDownloadWorker(QRunnable):
    """Download the selected search model, then hand over to index building."""

    def __init__(
        self,
        spec,
        *,
        tasks,
        status_callback: Callable[[str, int], None],
        on_downloaded: Callable[[], None],
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._spec = spec
        self._tasks = tasks
        self._status_callback = status_callback
        self._on_downloaded = on_downloaded

    @Slot()
    def run(self) -> None:
        from iPhoto.agent.infrastructure.search_model_download import (
            download_search_model,
        )

        self._tasks.start("download", tr("status.task.download"))
        try:
            download_search_model(
                self._spec,
                progress=lambda _fraction, message: self._status_callback(message, 5000),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._tasks.finish("download", failed=True)
            self._status_callback(
                f"{tr('search.model.download_failed')}: {exc}", 15000
            )
            return
        self._tasks.finish("download")
        self._status_callback(
            tr("search.model.download_done", name=self._spec.dir_name), 5000
        )
        self._on_downloaded()


class EmbeddingIndexWorker(QRunnable):
    """Build the semantic index silently, reporting progress in the status bar."""

    def __init__(
        self,
        session,
        *,
        model_spec,
        status,
        tasks,
        status_callback: Callable[[str], None],
        force_rebuild: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._session = session
        self._model_spec = model_spec
        self._status = status
        self._tasks = tasks
        self._status_callback = status_callback
        self._force_rebuild = bool(force_rebuild)

    @Slot()
    def run(self) -> None:
        try:
            embedding_service = self._session.get_embedding_service()
            if embedding_service is None:
                return

            embedding_repo = self._session.get_embedding_repository()
            if embedding_repo is None:
                return

            # Check how many need processing
            all_assets = self._session.asset_runtime.assets.read_all()
            image_assets = [asset for asset in all_assets if asset.get("media_type") == 0]
            asset_ids = [asset["id"] for asset in image_assets]

            pending = embedding_repo.get_asset_ids_without_embeddings(
                asset_ids,
                model_name=self._model_spec.dir_name,
            )

            if not pending:
                total = embedding_repo.count()
                self._status.set_indexing(False, len(asset_ids), len(asset_ids))
                self._status_callback(f"AI 索引已就绪 ({total} 张照片)")
                return

            from iPhoto.gui.ui.tasks.embedding_worker import EmbeddingWorker

            worker = EmbeddingWorker(
                embedding_service=embedding_service,
                embedding_repository=embedding_repo,
                asset_repository=self._session.asset_runtime.assets,
                library_root=self._session.library_root,
                force_rebuild=self._force_rebuild,
            )

            tasks = self._tasks
            status = self._status

            tasks.start("index", tr("status.task.index"))
            status.set_indexing(True, 0, int(len(pending)))

            def on_progress(current, total, msg):
                tasks.update(
                    "index", done=int(current), total=int(total), detail=str(msg)
                )
                status.set_indexing(True, current, total)

            def on_finished(processed, failed):
                tasks.finish("index", failed=bool(failed))
                status.set_indexing(False, len(asset_ids), len(asset_ids))
                self._status_callback(f"AI 索引生成完成 ({processed} 张照片)")

            worker.signals.progress.connect(on_progress)
            worker.signals.finished.connect(on_finished)

            QThreadPool.globalInstance().start(worker)

        except Exception as exc:  # noqa: BLE001 - the silent path only logs
            _LOGGER.debug("Silent embedding generation failed: %s", exc)


class EmbeddingInitWorker(QRunnable):
    """Build the semantic index with visible progress (first search)."""

    def __init__(
        self,
        session,
        *,
        model_spec,
        callback: Callable[[str, int, int, str], None],
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._session = session
        self._model_spec = model_spec
        self._callback = callback

    @Slot()
    def run(self) -> None:
        try:
            # Step 1: Load CLIP model
            self._callback("loading_model", 0, 0, "正在加载 AI 模型（约350MB）...")

            from iPhoto.agent.services import search_models

            model_dir = search_models.model_dir(self._model_spec)
            if not search_models.is_installed(self._model_spec):
                self._callback("error", 0, 0, "模型文件不存在，请先下载模型")
                return
            _LOGGER.debug("Embedding index will load the model from %s", model_dir)

            self._callback("model_loading", 0, 0, "模型文件已找到，正在加载...")

            # Load model with timeout tracking
            start_time = time.time()
            embedding_service = self._session.get_embedding_service()
            load_time = int(time.time() - start_time)

            if embedding_service is None or not embedding_service.is_loaded():
                self._callback(
                    "error", 0, 0, f"AI 模型加载失败（耗时 {load_time} 秒）"
                )
                return

            self._callback(
                "model_loaded", 0, 0, f"AI 模型加载完成（耗时 {load_time} 秒）"
            )

            # Step 2: Get embedding repository
            embedding_repo = self._session.get_embedding_repository()
            if embedding_repo is None:
                self._callback("error", 0, 0, "数据库初始化失败")
                return

            # Step 3: Count images to process
            self._callback("counting", 0, 0, "正在统计照片数量...")
            all_assets = self._session.asset_runtime.assets.read_all()
            image_assets = [asset for asset in all_assets if asset.get("media_type") == 0]
            asset_ids = [asset["id"] for asset in image_assets]
            pending_ids = embedding_repo.get_asset_ids_without_embeddings(asset_ids)

            if not pending_ids:
                self._callback("done", 0, 0, "")
                return

            self._callback(
                "starting",
                len(pending_ids),
                len(pending_ids),
                f"共 {len(pending_ids)} 张照片需要处理",
            )

            # Step 4: Start embedding generation
            from iPhoto.gui.ui.tasks.embedding_worker import EmbeddingWorker

            worker = EmbeddingWorker(
                embedding_service=embedding_service,
                embedding_repository=embedding_repo,
                asset_repository=self._session.asset_runtime.assets,
                library_root=self._session.library_root,
            )

            def on_progress(current, total, msg):
                self._callback("progress", current, total, msg)

            def on_finished(processed, failed):
                self._callback("done", processed, failed, "")

            worker.signals.progress.connect(on_progress)
            worker.signals.finished.connect(on_finished)
            worker.signals.error.connect(
                lambda err: self._callback("error", 0, 0, err)
            )

            QThreadPool.globalInstance().start(worker)

        except Exception as exc:  # noqa: BLE001 - reported through the callback
            import traceback

            _LOGGER.error(
                "Embedding init failed: %s\n%s", exc, traceback.format_exc()
            )
            self._callback("error", 0, 0, f"{str(exc)[:100]}")


__all__ = [
    "EmbeddingIndexWorker",
    "EmbeddingInitWorker",
    "SearchModelDownloadWorker",
]
