"""Background worker for generating image embeddings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from ....i18n import tr

_LOGGER = logging.getLogger(__name__)

_BATCH_SIZE = 16


class EmbeddingWorkerSignals(QObject):
    """Signals for the embedding worker."""

    progress = Signal(int, int, str)
    finished = Signal(int, int)
    error = Signal(str)


class EmbeddingWorker(QRunnable):
    """Background worker for generating image embeddings."""

    def __init__(
        self,
        embedding_service,
        embedding_repository,
        asset_repository,
        library_root: Path,
        force_rebuild: bool = False,
        model_name: str | None = None,
    ) -> None:
        """Initialize the embedding worker."""
        super().__init__()
        self.setAutoDelete(True)

        self._embedding_service = embedding_service
        self._embedding_repository = embedding_repository
        self._asset_repository = asset_repository
        self._library_root = library_root
        self._force_rebuild = force_rebuild
        self._model_name = model_name
        self._cancelled = False

        self.signals = EmbeddingWorkerSignals()


    def cancel(self) -> None:
        """Cancel the embedding generation."""
        self._cancelled = True

    def run(self) -> None:
        """Run the embedding generation process."""
        try:
            self._generate_embeddings()
        except Exception as exc:
            _LOGGER.exception("Embedding generation failed: %s", exc)
            self.signals.error.emit(str(exc))

    def _generate_embeddings(self) -> None:
        """Generate embeddings for all assets."""
        # Get all asset IDs from the repository
        all_assets = self._asset_repository.read_all()
        if not all_assets:
            _LOGGER.info("No assets found for embedding generation.")
            self.signals.finished.emit(0, 0)
            return

        # Filter to only image assets
        image_assets = [
            asset for asset in all_assets
            if asset.get("media_type") == 0  # 0 = image
        ]

        if not image_assets:
            _LOGGER.info("No image assets found for embedding generation.")
            self.signals.finished.emit(0, 0)
            return

        # Get asset IDs that need embeddings
        asset_ids = [asset["id"] for asset in image_assets]

        if self._force_rebuild:
            asset_ids_to_process = asset_ids
        else:
            asset_ids_to_process = self._embedding_repository.get_asset_ids_without_embeddings(
                asset_ids,
                model_name=self._model_name or "clip-vit-base-patch32",
            )

        if not asset_ids_to_process:
            _LOGGER.info("All images already have embeddings.")
            self.signals.finished.emit(0, 0)
            return

        # Create a lookup map for asset data
        asset_map = {asset["id"]: asset for asset in image_assets}

        total = len(asset_ids_to_process)
        processed = 0
        failed = 0

        self.signals.progress.emit(0, total, "开始生成嵌入向量...")

        # Process in batches
        for i in range(0, total, _BATCH_SIZE):
            if self._cancelled:
                _LOGGER.info("Embedding generation cancelled.")
                break

            batch_ids = asset_ids_to_process[i:i + _BATCH_SIZE]
            batch_paths = []

            for asset_id in batch_ids:
                asset = asset_map.get(asset_id)
                if asset:
                    rel_path = asset.get("rel", "")
                    abs_path = self._library_root / rel_path
                    if abs_path.exists():
                        batch_paths.append((asset_id, abs_path))
                    else:
                        failed += 1

            if not batch_paths:
                continue

            # Generate embeddings for batch
            paths = [p for _, p in batch_paths]
            embeddings = self._embedding_service.encode_images(paths)

            # Store successful embeddings
            to_store = []
            for (asset_id, _), embedding in zip(batch_paths, embeddings):
                if embedding is not None:
                    to_store.append((asset_id, embedding, self._model_name or "clip-vit-base-patch32"))
                    processed += 1
                else:
                    failed += 1

            if to_store:
                self._embedding_repository.store_embeddings_batch(to_store)

            # Report progress
            current = min(i + _BATCH_SIZE, total)
            self.signals.progress.emit(
                current,
                total,
                tr("status.embedding.progress", current=current, total=total),
            )

        self.signals.finished.emit(processed, failed)
        _LOGGER.info(
            "Embedding generation complete: %d processed, %d failed",
            processed,
            failed,
        )
