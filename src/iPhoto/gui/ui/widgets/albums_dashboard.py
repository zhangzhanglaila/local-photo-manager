"""Dashboard view displaying all user albums."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from ....i18n import tr

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
    QEvent,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPixmap,
    QPalette,
)
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from ....errors import LibraryError
from ....media_classifier import get_media_type, MediaType
from ....application.services.album_manifest_service import Album
from ....utils.pathutils import ensure_work_dir
from ...menus.album_sidebar_menu import _create_styled_input_dialog
from ..tasks.thumbnail_loader import ThumbnailJob, generate_cache_path, stat_mtime_ns
from ...menus.core import MenuActionSpec, MenuContext, populate_menu
from ...menus.style import apply_menu_style
from . import dialogs
from .albums_dashboard_widgets import AlbumCard, RoundedImageView  # noqa: F401 - re-export
from .flow_layout import FlowLayout
from ....utils.pathutils import paths_equal

if TYPE_CHECKING:
    from ....infrastructure.services.library_asset_query_service import LibraryAssetQueryService
    from ....library.runtime_controller import LibraryRuntimeController
    from ....library.tree import AlbumNode



class DashboardLoaderSignals(QObject):
    """Signals for the dashboard data loader."""

    albumReady = Signal(object, int, object, object, int)  # node, count, cover_path, album_root, generation


class AlbumDataWorker(QRunnable):
    """Background worker to fetch metadata (count, cover path) for an album."""

    def __init__(
        self,
        node: AlbumNode,
        signals: DashboardLoaderSignals,
        generation: int,
        library_root: Optional[Path] = None,
        asset_query_service: LibraryAssetQueryService | None = None,
    ) -> None:
        super().__init__()
        self.node = node
        self.signals = signals
        self.generation = generation
        self._library_root = library_root
        self._asset_query_service = asset_query_service

    def run(self) -> None:
        # 1. Get count and first asset for cover fallback
        count = 0
        first_rel: str | None = None

        try:
            index_root = self._library_root if self._library_root else self.node.path
            query_service = self._asset_query_service
            if query_service is None:
                raise RuntimeError(
                    "Active library session is unavailable; album dashboard queries "
                    "require a bound LibrarySession."
                )

            count = query_service.count_assets(self.node.path)

            for row in query_service.read_asset_rows(self.node.path):
                if isinstance(row, dict):
                    rel = row.get("rel", "")
                    if isinstance(rel, str) and rel:
                        first_rel = rel
                        break
        except Exception:
            pass

        # 2. Determine cover path
        cover_path: Path | None = None
        try:
            album = Album.open(self.node.path)
            cover_rel = album.manifest.get("cover")
            if cover_rel:
                candidate = self.node.path / cover_rel
                if candidate.exists():
                    cover_path = candidate
        except Exception:
            pass

        if cover_path is None and first_rel:
            candidate = self.node.path / first_rel
            if candidate.exists():
                cover_path = candidate

        self.signals.albumReady.emit(self.node, count, cover_path, self.node.path, self.generation)


class DashboardThumbnailLoader(QObject):
    """Simplified thumbnail loader for dashboard cards."""

    thumbnailReady = Signal(Path, Path, QPixmap)  # album_root, source_path, pixmap
    _delivered = Signal(tuple, QImage, str)  # key (album_root_str, rel, width, height, stamp), image, rel

    def __init__(self, parent: QObject | None = None, library_root: Optional[Path] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._delivered.connect(self._handle_result)
        # Map base keys (album_root_str, rel, width, height) to queued album root Paths
        self._key_to_root: dict[tuple[str, str, int, int], deque[Path]] = {}
        self._resolved_roots: dict[Path, str] = {}
        self._library_root = library_root

    def request_with_absolute_key(self, album_root: Path, image_path: Path, size: QSize) -> None:
        # To avoid rel collision across albums, we use the absolute path string as the 'rel' identifier
        # passed to ThumbnailJob. This ensures the key emitted back is unique.
        unique_rel = str(image_path)

        # Use library root if available, otherwise fallback to album root
        effective_library_root = self._library_root if self._library_root else album_root

        try:
            work_dir = ensure_work_dir(effective_library_root)
            thumbs_dir = work_dir / "thumbs"
            thumbs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        try:
            stat = image_path.stat()
        except OSError:
            return
        stamp = stat_mtime_ns(stat)

        # Use standardized generator with absolute path
        cache_path = generate_cache_path(effective_library_root, image_path, size, stamp)

        if cache_path.exists():
            pixmap = QPixmap(str(cache_path))
            if not pixmap.isNull():
                self.thumbnailReady.emit(album_root, image_path, pixmap)
                return

        # Store mapping
        job_root_str = self._album_root_str(album_root)
        base_key: tuple[str, str, int, int] = (job_root_str, unique_rel, size.width(), size.height())
        self._key_to_root.setdefault(base_key, deque()).append(album_root)

        media_type = get_media_type(image_path)
        is_image = media_type == MediaType.IMAGE
        is_video = media_type == MediaType.VIDEO

        # Determine cache_rel based on library root if possible to match main loader behavior,
        # but DashboardThumbnailLoader logic uses unique_rel as the key.
        # We pass effective_library_root as library_root to ThumbnailJob.

        job = ThumbnailJob(
            self,  # type: ignore
            unique_rel,  # Pass absolute path string as rel to ensure uniqueness
            image_path,
            size,
            None,  # Pass None as known_stamp to force regeneration if missing
            album_root,
            effective_library_root,
            is_image=is_image,
            is_video=is_video,
            still_image_time=None,
            duration=None,
            cache_rel=None, # Not used when hashing absolute path in new logic?
            # Wait, ThumbnailJob still uses _cache_rel if provided?
            # In new logic: rel_for_path = self._cache_rel if self._cache_rel is not None else self._rel
            # Then: generate_cache_path(self._library_root, self._abs_path, ...)
            # generate_cache_path IGNORES rel/cache_rel now! It uses abs_path.
            # So cache_rel is irrelevant for path generation, but might be used for logging?
            # The job passes it. Let's pass None or keep it consistent?
            # The old code calculated real_rel.
            # Let's pass None as it's not needed for the path generation anymore.
        )
        self._pool.start(job)

    def _handle_result(
        self, full_key: tuple[str, str, int, int, int], image: Optional[QImage], rel: str
    ) -> None:
        # Use the base key (without timestamp) by slicing off the stamp so sidecar or filesystem
        # timestamp changes do not prevent delivered thumbnails from matching pending requests.
        base_key = full_key[:-1]
        roots = self._key_to_root.get(base_key)
        if not roots:
            return
        album_root = roots.popleft()
        if not roots:
            self._key_to_root.pop(base_key, None)

        if image is None:
            return

        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self.thumbnailReady.emit(album_root, Path(rel), pixmap)

    def _album_root_str(self, album_root: Path) -> str:
        cached = self._resolved_roots.get(album_root)
        if cached is not None:
            return cached
        try:
            resolved_path = album_root.resolve()
        except OSError:
            resolved_path = album_root
        resolved = str(resolved_path)
        self._resolved_roots[album_root] = resolved
        return resolved


class AlbumsDashboard(QWidget):
    """Main view for browsing all user albums."""

    albumSelected = Signal(Path)

    def __init__(self, library: LibraryRuntimeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._pinned_service: PinnedItemsService | None = None
        self._cards: dict[Path, AlbumCard] = {}
        self._album_nodes: dict[Path, AlbumNode] = {}
        self._requested_cover_paths: dict[Path, Path] = {}
        # Track refresh generation to prevent race conditions
        # Python integers can grow arbitrarily large, so overflow is not a concern
        self._current_generation = 0

        # Setup loader
        self._loader_signals = DashboardLoaderSignals()
        self._loader_signals.albumReady.connect(self._on_album_data_ready)

        self._thumb_loader = DashboardThumbnailLoader(self, library_root=self._library.root())
        self._thumb_loader.thumbnailReady.connect(self._on_thumbnail_ready)

        self._init_ui()
        self._library.treeUpdated.connect(self.refresh)
        self._library.scanFinished.connect(self._on_scan_finished)
        self.refresh()

    def set_pinned_service(self, service: PinnedItemsService | None) -> None:
        self._pinned_service = service

    def _init_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(40, 40, 40, 40)
        self.main_layout.setSpacing(20)

        # Header
        self.header_label = QLabel("Albums")
        font = QFont()
        font.setPixelSize(22)
        font.setBold(True)
        self.header_label.setFont(font)
        self.header_label.setStyleSheet("margin-bottom: 10px;")
        self.main_layout.addWidget(self.header_label)

        # Scroll Area for the grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background: transparent;")

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.flow_layout = FlowLayout(self.scroll_content, margin=0, h_spacing=20, v_spacing=20)
        self.scroll_content.setLayout(self.flow_layout)

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        # Empty state placeholder
        self.empty_label = QLabel(self.tr("No albums available"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("font-size: 16px;")
        self.empty_label.hide()
        self.main_layout.addWidget(self.empty_label)
        self._apply_theme()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_theme()
        super().changeEvent(event)

    def refresh(self) -> None:
        # Increment generation to invalidate pending workers from previous refresh
        self._current_generation += 1
        # Clear existing
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._cards.clear()
        self._album_nodes.clear()
        self._requested_cover_paths.clear()

        albums = self._library.list_albums()

        if not albums:
            self.scroll_area.hide()
            self.empty_label.show()
            return

        self.empty_label.hide()
        self.scroll_area.show()

        pool = QThreadPool.globalInstance()
        current_gen = self._current_generation
        library_root = self._library.root()

        for album in albums:
            # Create card with "0" count first
            card = AlbumCard(album.path, album.title, 0, self.scroll_content)
            card.clicked.connect(self.albumSelected)
            card.menuRequested.connect(self._show_card_menu)
            self.flow_layout.addWidget(card)
            self._cards[album.path] = card
            self._album_nodes[album.path] = album

            # Fetch data with the current session query surface when available.
            worker = AlbumDataWorker(
                album,
                self._loader_signals,
                current_gen,
                library_root=library_root,
                asset_query_service=getattr(
                    self._library,
                    "asset_query_service",
                    None,
                ),
            )
            pool.start(worker)

    def _on_album_data_ready(
        self, node: AlbumNode, count: int, cover_path: Path | None, root: Path, generation: int
    ) -> None:
        # Ignore results from outdated refresh operations
        if generation != self._current_generation:
            return
        card = self._cards.get(root)
        if not card:
            return

        # Update count
        card.count_label.setText(str(count))

        # Load cover
        if cover_path:
            self._requested_cover_paths[root] = cover_path
            self._thumb_loader.request_with_absolute_key(root, cover_path, QSize(512, 512))

    def _on_thumbnail_ready(self, album_root: Path, source_path: Path, pixmap: QPixmap) -> None:
        expected = self._requested_cover_paths.get(album_root)
        if expected is not None and not self._paths_equal(expected, source_path):
            return
        card = self._card_for_album_root(album_root)
        if card:
            card.set_cover_image(pixmap)

    def _on_scan_finished(self, root: Path, success: bool) -> None:
        if not success:
            return
        try:
            library_root = self._library.root().resolve()
            scan_root = root.resolve()
        except OSError:
            return

        extra_roots: list[Path] = []
        try:
            extra_roots = [Path(root).resolve() for root in self._library.roots()]
        except Exception:
            extra_roots = []
        if (
            scan_root == library_root
            or library_root in scan_root.parents
            or scan_root in extra_roots
        ):
            # Counts change when any root finishes scanning, including a second
            # root that lives outside the primary one.
            self.refresh()

    def _show_card_menu(self, album_path: Path, global_pos) -> None:
        card = self._cards.get(album_path)
        if card is None:
            return
        menu = self._build_card_menu(card)
        menu.exec(global_pos)

    def update_album_cover(self, album_root: Path, cover_path: Path) -> None:
        card = self._card_for_album_root(album_root)
        if card is None or not cover_path.exists():
            return
        self._requested_cover_paths[card.path] = cover_path
        pixmap = QPixmap(str(cover_path))
        if not pixmap.isNull():
            card.set_cover_image(pixmap)
        self._thumb_loader.request_with_absolute_key(card.path, cover_path, QSize(512, 512))

    def _build_card_menu(self, card: AlbumCard) -> QMenu:
        menu = QMenu(self)
        apply_menu_style(menu, self)
        populate_menu(
            menu,
            context=MenuContext(
                surface="albums_dashboard",
                selection_kind="empty",
                entity_kind="album",
                entity_id=str(card.path),
                active_root=card.path,
            ),
            action_specs=[
                MenuActionSpec(
                    action_id="rename_album",
                    label="Rename…",
                    on_trigger=lambda _ctx: self._prompt_rename_album(card),
                ),
                MenuActionSpec(
                    action_id="toggle_album_pin",
                    label="Unpin Album" if self._is_album_pinned(card.path) else "Pin Album",
                    on_trigger=lambda _ctx: self._toggle_album_pin(card),
                    is_enabled=lambda _ctx: self._pin_actions_available(),
                ),
            ],
            anchor=self,
        )
        return menu

    def _toggle_album_pin(self, card: AlbumCard) -> None:
        if self._pinned_service is None:
            return
        library_root = self._library.root()
        if library_root is None:
            return
        if self._is_album_pinned(card.path):
            self._pinned_service.unpin(
                kind="album",
                item_id=str(card.path),
                library_root=library_root,
            )
            return
        self._pinned_service.pin_album(
            card.path,
            card.title,
            library_root=library_root,
        )

    def _prompt_rename_album(self, card: AlbumCard) -> None:
        album = self._album_node_for_path(card.path)
        if album is None:
            return
        name, ok = _create_styled_input_dialog(
            self,
            "Rename Album",
            "New album name:",
            text=card.title,
        )
        if not ok:
            return
        target_name = name.strip()
        if not target_name:
            dialogs.show_warning(self, tr("msg.album_name_empty"))
            return
        try:
            self._library.rename_album(album, target_name)
        except LibraryError as exc:  # pragma: no cover - GUI feedback
            dialogs.show_warning(self, str(exc))

    def _is_album_pinned(self, album_path: Path) -> bool:
        if self._pinned_service is None:
            return False
        return self._pinned_service.is_pinned(
            kind="album",
            item_id=str(album_path),
            library_root=self._library.root(),
        )

    def _pin_actions_available(self) -> bool:
        return self._pinned_service is not None and self._library.root() is not None

    def _card_for_album_root(self, album_root: Path) -> AlbumCard | None:
        card = self._cards.get(album_root)
        if card is not None:
            return card
        for path, existing in self._cards.items():
            if self._paths_equal(path, album_root):
                return existing
        return None

    def _album_node_for_path(self, album_path: Path) -> AlbumNode | None:
        album = self._album_nodes.get(album_path)
        if album is not None:
            return album
        for path, existing in self._album_nodes.items():
            if self._paths_equal(path, album_path):
                return existing
        return None

    def _paths_equal(self,first,second) -> bool:
        """Delegate to the single shared comparison."""

        return paths_equal(first,second)

    def _apply_theme(self) -> None:
        app = QGuiApplication.instance()
        palette = app.palette() if app else self.palette()
        text_primary = palette.color(QPalette.ColorRole.Text)
        text_secondary = QColor(text_primary)
        text_secondary.setAlpha(160)

        self.header_label.setStyleSheet(
            "color: "
            f"{text_primary.name(QColor.NameFormat.HexArgb)}; "
            "margin-bottom: 10px;"
        )
        self.empty_label.setStyleSheet(
            "color: "
            f"{text_secondary.name(QColor.NameFormat.HexArgb)}; "
            "font-size: 16px;"
        )
        for card in self._cards.values():
            card.apply_theme()
