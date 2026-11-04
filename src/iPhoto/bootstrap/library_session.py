"""Library-scoped runtime session for vNext application boundaries."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..application.ports import (
    AssetRepositoryPort,
    AssetStateServicePort,
    EditServicePort,
    LibraryStateRepositoryPort,
    LocationAssetServicePort,
    MapInteractionServicePort,
    MapRuntimePort,
)
from ..application.services.map_interaction_service import LibraryMapInteractionService
from ..infrastructure.repositories.library_state_repository import (
    IndexStoreLibraryStateRepository,
)
from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
from ..infrastructure.services.location_metadata_service import (
    ExifToolLocationMetadataService,
)
from ..infrastructure.services.map_runtime_service import SessionMapRuntimeService
from ..application.services.assign_location_service import AssignLocationService
from ..people.service import PeopleService
from .library_asset_state_service import LibraryAssetStateService
from .library_album_metadata_service import LibraryAlbumMetadataService
from .library_asset_lifecycle_service import LibraryAssetLifecycleService
from .library_asset_operation_service import LibraryAssetOperationService
from .library_asset_query_service import LibraryAssetQueryService
from .library_edit_service import LibraryEditService
from .library_location_service import LibraryLocationService
from .library_people_service import create_people_service
from .library_scan_service import LibraryScanService

logger = logging.getLogger(__name__)


@dataclass
class LibrarySession:
    """Own library-scoped adapters and expose the application-facing surface."""

    library_root: Path
    asset_runtime: LibraryAssetRuntime | None = None
    state_repository: LibraryStateRepositoryPort | None = None
    asset_state: AssetStateServicePort | None = None
    album_metadata: LibraryAlbumMetadataService | None = None
    asset_queries: LibraryAssetQueryService | None = None
    scans: LibraryScanService | None = None
    asset_lifecycle: LibraryAssetLifecycleService | None = None
    asset_operations: LibraryAssetOperationService | None = None
    people: PeopleService | None = None
    maps: MapRuntimePort | None = None
    map_interactions: MapInteractionServicePort | None = None
    edit: EditServicePort | None = None
    locations: LocationAssetServicePort | None = None
    bind_asset_runtime: bool = True
    # Agent services (optional, initialized lazily)
    _search_service: object = field(default=None, repr=False)
    _embedding_service: object = field(default=None, repr=False)
    _embedding_repository: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.library_root = Path(self.library_root)
        if self.asset_runtime is None:
            self.asset_runtime = LibraryAssetRuntime(self.library_root)
            self.bind_asset_runtime = False
        if self.bind_asset_runtime:
            self.asset_runtime.bind_library_root(self.library_root)
        if self.state_repository is None:
            self.state_repository = IndexStoreLibraryStateRepository(self.library_root)
        if self.asset_queries is None:
            self.asset_queries = LibraryAssetQueryService(self.library_root)
        if self.asset_state is None:
            self.asset_state = LibraryAssetStateService(
                self.library_root,
                state_repository=self.state_repository,
                favorite_query=self.asset_queries,
            )
        if self.album_metadata is None:
            self.album_metadata = LibraryAlbumMetadataService(
                self.library_root,
                state_repository=self.state_repository,
            )
        if self.scans is None:
            self.scans = LibraryScanService(self.library_root)
        if self.asset_lifecycle is None:
            self.asset_lifecycle = LibraryAssetLifecycleService(
                self.library_root,
                scan_service=self.scans,
            )
        if self.asset_operations is None:
            self.asset_operations = LibraryAssetOperationService(
                self.library_root,
                lifecycle_service=self.asset_lifecycle,
            )
        if self.people is None:
            self.people = create_people_service(self.library_root)
        if self.maps is None:
            self.maps = SessionMapRuntimeService()
        if self.map_interactions is None:
            self.map_interactions = LibraryMapInteractionService()
        if self.edit is None:
            self.edit = LibraryEditService(self.library_root)
        if self.locations is None:
            self.locations = LibraryLocationService(
                self.library_root,
                query_service=self.asset_queries,
            )
        bind_edit_service = getattr(self.asset_runtime, "bind_edit_service", None)
        if callable(bind_edit_service):
            bind_edit_service(self.edit)

    def get_search_service(self):
        """Get or initialize the search service.

        Returns
        -------
        SearchService or None
            The search service, or None if CLIP model is not available.
        """
        if self._search_service is not None:
            return self._search_service

        try:
            from ..agent.services.search_service import SearchService

            # Get embedding service and repository
            embedding_service = self.get_embedding_service()
            embedding_repo = self.get_embedding_repository()

            if embedding_service is None or embedding_repo is None:
                return None

            # Note: Don't check is_loaded() here - model loads lazily on first use
            # The search will trigger model loading when needed

            # Create search service with CLIP
            self._search_service = SearchService(
                embedding_service=embedding_service,
                asset_repository=self.asset_runtime.assets,
                embedding_repository=embedding_repo,
            )

            return self._search_service

        except Exception as e:
            logger.warning("Failed to initialize search service: %s", e)
            return None

    def get_embedding_repository(self):
        """Get or initialize the embedding repository."""
        if self._embedding_repository is not None:
            return self._embedding_repository

        try:
            from ..cache.index_store.embedding_repository import get_embedding_repository
            self._embedding_repository = get_embedding_repository(self.library_root)
            return self._embedding_repository
        except Exception as e:
            logger.warning("Failed to initialize embedding repository: %s", e)
            return None

    def get_embedding_service(self):
        """Get or initialize the embedding service."""
        if self._embedding_service is not None:
            return self._embedding_service

        try:
            from ..agent.infrastructure.clip_embedding import CLIPEmbeddingService
            from ..agent.infrastructure.clip_downloader import get_model_path, is_model_available, get_model_dir

            model_dir = get_model_dir(self.library_root)
            logger.info(f"Checking CLIP model at: {model_dir}")

            # Check if model exists
            if not is_model_available(model_dir):
                logger.info(f"CLIP model not found at {model_dir}, showing download dialog")
                self._show_model_download_dialog(model_dir)
                return None

            # Load model
            model_path = get_model_path(self.library_root)
            logger.info(f"Loading CLIP model from: {model_path}")
            self._embedding_service = CLIPEmbeddingService(model_dir=model_path)
            return self._embedding_service
        except Exception as e:
            logger.warning("Failed to initialize embedding service: %s", e)
            return None

    def _show_model_download_dialog(self, model_dir: Path) -> None:
        """Show dialog to user about missing CLIP model."""
        try:
            from PySide6.QtWidgets import QMessageBox
            from ..agent.infrastructure.clip_downloader import get_download_instructions, download_model

            model_path = model_dir / "clip-vit-base-patch32"

            msg = QMessageBox()
            msg.setWindowTitle("语义搜索需要下载模型")
            msg.setText(
                "语义搜索功能需要下载 CLIP 模型（约 350MB）。\n\n"
                f"模型目录: {model_path}\n\n"
                "是否现在下载？"
            )
            msg.setInformativeText(
                "下载后可以搜索任意内容，如：\n"
                "- 黄鹤楼、海边、日落\n"
                "- 狗、猫、美食\n"
                "- 穿红衣服的人"
            )

            # Add custom buttons
            auto_button = msg.addButton("自动下载", QMessageBox.ButtonRole.AcceptRole)
            manual_button = msg.addButton("手动下载", QMessageBox.ButtonRole.ActionRole)
            cancel_button = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)

            msg.exec()

            clicked = msg.clickedButton()

            if clicked == auto_button:
                self._download_model(model_dir)
            elif clicked == manual_button:
                self._show_manual_download_instructions(model_dir)

        except Exception as e:
            logger.error("Failed to show download dialog: %s", e)

    def _download_model(self, model_dir: Path) -> None:
        """Download CLIP model in background."""
        try:
            from PySide6.QtCore import QThread, Signal
            from PySide6.QtWidgets import QProgressDialog
            from ..agent.infrastructure.clip_downloader import download_model

            class DownloadThread(QThread):
                progress_updated = Signal(int, int, str)
                finished = Signal(bool)

                def __init__(self, model_dir):
                    super().__init__()
                    self._model_dir = model_dir

                def run(self):
                    def progress_callback(current, total, message):
                        self.progress_updated.emit(current, total, message)

                    success = download_model(
                        model_dir=self._model_dir,
                        progress_callback=progress_callback,
                    )
                    self.finished.emit(success)

            # Create non-blocking progress dialog
            progress = QProgressDialog("正在下载 CLIP 模型...", "后台下载", 0, 100)
            progress.setWindowTitle("下载模型")
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)

            # Create and start download thread
            self._download_thread = DownloadThread(model_dir)

            # Connect signals
            self._download_thread.progress_updated.connect(
                lambda current, total, msg: (
                    progress.setValue(current),
                    progress.setLabelText(msg)
                )
            )

            self._download_thread.finished.connect(
                lambda success: self._on_download_finished(success, progress, model_dir)
            )

            # Show dialog non-blocking
            progress.show()

            # Start download
            self._download_thread.start()

        except Exception as e:
            logger.error("Failed to start download: %s", e)

    def _on_download_finished(self, success: bool, progress, model_dir: Path = None) -> None:
        """Handle download completion."""
        from PySide6.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QTextEdit, QPushButton, QApplication, QLabel, QHBoxLayout

        if success:
            progress.setLabelText("下载完成！")
            progress.setValue(100)
            QMessageBox.information(None, "下载完成", "CLIP 模型下载完成！\n\n请重新启动应用以使用语义搜索。")
        else:
            progress.setLabelText("下载失败")

            model_path = model_dir / "clip-vit-base-patch32" if model_dir else "项目目录/extension/models/clip-vit-base-patch32"

            # Create a dialog with selectable text
            dialog = QDialog()
            dialog.setWindowTitle("下载失败")
            dialog.setMinimumSize(650, 500)

            layout = QVBoxLayout(dialog)

            # Title
            title = QLabel("CLIP 模型下载失败")
            title.setStyleSheet("font-size: 14px; font-weight: bold;")
            layout.addWidget(title)

            # Error message with selectable text
            error_text = QTextEdit()
            error_text.setReadOnly(True)
            error_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
            error_text.setText(
                "可能原因：\n"
                "1. 网络连接问题\n"
                "2. HuggingFace 被屏蔽（需要VPN）\n"
                "3. 磁盘空间不足\n\n"
                "========================================\n"
                "手动下载方法（复制以下命令到CMD执行）：\n"
                "========================================\n\n"
                "【方法1】HuggingFace 镜像（推荐）\n"
                "----------------------------------------\n"
                "pip install huggingface_hub\n\n"
                "set HF_ENDPOINT=https://hf-mirror.com\n\n"
                f"python -c \"from huggingface_hub import snapshot_download; snapshot_download('openai/clip-vit-base-patch32', local_dir=r'{model_path}')\"\n\n\n"
                "【方法2】ModelScope（国内镜像）\n"
                "----------------------------------------\n"
                "pip install modelscope\n\n"
                f"python -c \"from modelscope import snapshot_download; snapshot_download('AI-ModelScope/clip-vit-base-patch32', cache_dir=r'{model_dir}')\"\n\n\n"
                f"下载位置：{model_path}\n\n"
                "下载完成后重启应用即可。"
            )
            layout.addWidget(error_text)

            # Buttons
            btn_layout = QHBoxLayout()

            copy_btn = QPushButton("复制全部内容")
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(error_text.toPlainText()))
            btn_layout.addWidget(copy_btn)

            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dialog.close)
            btn_layout.addWidget(close_btn)

            layout.addLayout(btn_layout)

            dialog.exec()

        progress.close()

    def _show_manual_download_instructions(self, model_dir: Path) -> None:
        """Show manual download instructions."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QApplication

        model_path = model_dir / "clip-vit-base-patch32"

        instructions = (
            "手动下载 CLIP 模型\n"
            "========================================\n\n"
            "下载位置（必须放这里）：\n"
            f"{model_path}\n\n"
            "方法1：使用 HuggingFace 镜像（推荐）\n"
            "----------------------------------------\n"
            "1. 打开命令行（CMD 或 PowerShell）\n"
            "2. 执行以下命令：\n\n"
            "pip install huggingface_hub\n"
            "set HF_ENDPOINT=https://hf-mirror.com\n"
            f"python -c \"from huggingface_hub import snapshot_download; snapshot_download('openai/clip-vit-base-patch32', local_dir=r'{model_path}')\"\n\n"
            "方法2：使用 ModelScope（国内镜像）\n"
            "----------------------------------------\n"
            "1. 打开命令行\n"
            "2. 执行以下命令：\n\n"
            "pip install modelscope\n"
            f"python -c \"from modelscope import snapshot_download; snapshot_download('AI-ModelScope/clip-vit-base-patch32', cache_dir=r'{model_dir}')\"\n\n"
            "方法3：手动下载文件\n"
            "----------------------------------------\n"
            "1. 访问 https://hf-mirror.com/openai/clip-vit-base-patch32\n"
            "2. 下载所有文件\n"
            f"3. 放到 {model_path} 目录下\n\n"
            "下载完成后重启应用即可使用语义搜索。"
        )

        dialog = QDialog()
        dialog.setWindowTitle("手动下载 CLIP 模型")
        dialog.setMinimumSize(700, 500)

        layout = QVBoxLayout(dialog)

        text_edit = QTextEdit()
        text_edit.setPlainText(instructions)
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(text_edit)

        # Button row
        btn_layout = QHBoxLayout()

        copy_btn = QPushButton("复制命令到剪贴板")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(instructions))
        btn_layout.addWidget(copy_btn)

        open_folder_btn = QPushButton("打开下载目录")
        open_folder_btn.clicked.connect(lambda: os.startfile(str(model_path)) if os.name == 'nt' else None)
        btn_layout.addWidget(open_folder_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        dialog.exec()

    @property
    def assets(self) -> AssetRepositoryPort:
        return self.asset_runtime.assets

    @property
    def thumbnails(self):
        return self.asset_runtime.thumbnail_service

    @property
    def state(self) -> LibraryStateRepositoryPort:
        assert self.state_repository is not None
        return self.state_repository

    def assign_location_service(self) -> AssignLocationService:
        return AssignLocationService(self.state, ExifToolLocationMetadataService())

    def shutdown(self) -> None:
        bind_edit_service = getattr(self.asset_runtime, "bind_edit_service", None)
        if callable(bind_edit_service):
            bind_edit_service(None)
        self.asset_runtime.shutdown()


def create_headless_library_session(root: Path) -> LibrarySession:
    """Create a library session for non-GUI entry points such as the CLI."""

    library_root = Path(root)
    return LibrarySession(
        library_root,
        asset_runtime=LibraryAssetRuntime(library_root),
        bind_asset_runtime=False,
        # A headless session has no map surface, so it must not load the native
        # widget DLL. That load is fatal rather than catchable when the DLL's
        # bundled Qt runtime conflicts with the one already loaded by a host
        # process that happens to own a QApplication (a test session, for
        # example), and it would take the whole process down.
        maps=SessionMapRuntimeService(allow_native_widget_probe=False),
    )


def create_library_state_repository(root: Path) -> LibraryStateRepositoryPort:
    """Create the current state adapter for compatibility entry points."""

    return IndexStoreLibraryStateRepository(Path(root))


__all__ = [
    "LibrarySession",
    "create_headless_library_session",
    "create_library_state_repository",
]
