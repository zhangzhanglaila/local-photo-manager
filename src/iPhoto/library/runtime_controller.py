"""Basic library runtime control: scanning, watching and editing albums.

This module acts as a coordinator/facade. The heavy lifting is delegated to
sub-modules extracted during a refactoring pass:

* :mod:`.album_operations`   – Album CRUD and manifest helpers
* :mod:`.scan_coordinator`   – Background scan scheduling & progress
* :mod:`.filesystem_watcher` – ``QFileSystemWatcher`` wrapper
* :mod:`.geo_aggregator`     – ``GeotaggedAsset`` dataclass & collection
* :mod:`.service_bindings`  – session-scoped service bindings (``bind_*``)
* :mod:`.trash_manager`      – Trash / deleted-items management
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal, QThreadPool, QMutex

from ..errors import LibraryUnavailableError
from ..people.index_coordinator import PeopleIndexCoordinator, get_people_index_coordinator
from ..utils.logging import get_logger
from .service_bindings import LibraryServiceBindingsMixin
from .service_registry import LibraryServiceRegistry
from .tree import AlbumNode

# Re-export GeotaggedAsset for presentation widgets that need the map DTO.
from .geo_aggregator import GeotaggedAsset  # noqa: F401

# Mixin classes providing the extracted functionality
from .album_operations import AlbumOperationsMixin
from .scan_coordinator import ScanCoordinatorMixin
from .filesystem_watcher import FileSystemWatcherMixin
from .geo_aggregator import GeoAggregatorMixin
from .trash_manager import TrashManagerMixin

# Workers are still needed for type annotations in __init__
from .workers.face_scan_worker import FaceScanWorker
from .workers.scanner_worker import ScannerWorker

LOGGER = get_logger()

if TYPE_CHECKING:  # pragma: no cover
    from ..bootstrap.library_session import LibrarySession


class LibraryRuntimeController(
    AlbumOperationsMixin,
    ScanCoordinatorMixin,
    FileSystemWatcherMixin,
    GeoAggregatorMixin,
    TrashManagerMixin,
    LibraryServiceBindingsMixin,
    QObject,
):
    """Manage the Basic Library tree, file-system helpers, and scanning state."""

    treeUpdated = Signal()
    albumRenamed = Signal(Path, Path)
    errorRaised = Signal(str)

    # Scanner signals exposed for the facade
    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, object)
    scanFinished = Signal(Path, bool)
    scanBatchFailed = Signal(Path, int)
    peopleIndexUpdated = Signal()
    peopleSnapshotCommitted = Signal(object)
    faceScanStatusChanged = Signal(str)
    ocrScanStatusChanged = Signal(object)
    #: Machine-readable AI-scan lifecycle for the status-bar task list. The
    #: human-readable signals above carry sentences; these carry a feature key
    #: (``face``/``ocr``) plus counts so the GUI can show real progress.
    aiScanStarted = Signal(str)
    aiScanProgress = Signal(str, int, int)
    aiScanFinished = Signal(str, bool)
    aiScanCancelled = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        feature_switches: object | None = None,
        model_selection: object | None = None,
        session_factory: Callable[[Path], object] | None = None,
    ) -> None:
        super().__init__(parent)
        #: 设置 → 智能 的人脸/OCR 开关；由 bootstrap 注入，缺省视为开启。
        self._feature_switches = feature_switches
        #: Which model pack each AI feature uses; injected by bootstrap.
        self._model_selection = model_selection
        #: Builds the headless session bound by :meth:`bind_path`; injected by
        #: bootstrap so this module never reaches into the composition root.
        self._session_factory = session_factory
        self._roots: list[Path] = []
        self._albums: list[AlbumNode] = []
        self._children: Dict[Path, list[AlbumNode]] = {}
        self._nodes: Dict[Path, AlbumNode] = {}
        self._deleted_dir: Path | None = None
        self._watcher = QFileSystemWatcher(self)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(500)
        self._pending_watch_paths: set[Path] = set()
        self._watch_scan_queue: list[Path] = []
        # ⭐ 防抖：为人脸扫描UI更新添加防抖机制，避免频繁刷新导致卡顿
        self._people_update_debounce = QTimer(self)
        self._people_update_debounce.setSingleShot(True)
        self._people_update_debounce.setInterval(2000)  # 默认2秒防抖
        self._people_update_debounce.timeout.connect(self._emit_people_update_deferred)
        # ⭐ 前台浏览状态跟踪：看照片/播视频时让后台扫描让路
        self._foreground_viewing_count = 0  # 当前处于前台浏览状态的计数
        # ``_watch_suspend_depth`` tracks how many in-flight operations asked us to
        # ignore file-system notifications. We use a counter instead of a boolean
        # to correctly handle nested operations that may overlap (e.g., multiple
        # concurrent file operations that each need to pause/resume the watcher).
        self._watch_suspend_depth = 0
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._debounce.timeout.connect(self._on_watcher_debounce_timeout)
        self.scanFinished.connect(self._on_watcher_scan_finished)

        # Scanner State
        self._current_scanner_worker: Optional[ScannerWorker] = None
        #: Monotonic token for the active file scan. A new scan increments it, so
        #: an earlier scan's queued ``finished`` signal cannot finalize the new
        #: scan or consume its pending AI-worker flags.
        self._scan_generation: int = 0
        self._current_face_scanner: Optional[FaceScanWorker] = None
        self._current_ocr_scanner = None
        self._scan_thread_pool = QThreadPool.globalInstance()
        self._live_scan_buffer: List[Dict] = []
        self._live_scan_root: Optional[Path] = None
        self._scan_buffer_lock = QMutex()
        self._geotagged_assets_cache: Optional[List[GeotaggedAsset]] = None
        self._geotagged_assets_cache_root: Optional[Path] = None
        self._face_scan_status_message: Optional[str] = None
        self._face_scan_pending_start: bool = False
        self._ocr_scan_pending_start: bool = False
        #: Whether the current AI worker has actually been handed to Qt yet.
        #: Sequential root scans queue rows into the same workers, so this is
        #: what distinguishes "still collecting rows" from "already running".
        self._face_scan_started: bool = False
        self._ocr_scan_started: bool = False
        self._people_index_coordinator: PeopleIndexCoordinator | None = None
        self._library_session: "LibrarySession | None" = None
        self._owns_library_session = False
        # Every session-scoped service lives in one immutable snapshot, so a
        # half-finished bind cannot look like a bound library.
        self._services = LibraryServiceRegistry()

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------
    def root(self) -> Path | None:
        return self._roots[0] if self._roots else None

    def roots(self) -> list[Path]:
        return list(self._roots)

    def _sync_workspace_roots(self) -> None:
        """Teach the path helpers which folders are library roots.

        Without this, opening an album allocated a whole workspace directory for
        it (``<workspace>/<album name>/global_index.db``), which is what turned a
        shared workspace into a pile of near-empty folders.
        """

        try:
            from ..utils.pathutils import set_known_library_roots

            set_known_library_roots(self._roots)
        except Exception:  # pragma: no cover - never block binding on this
            LOGGER.debug("could not register library roots for the workspace", exc_info=True)

    def add_root(self, path: Path) -> None:
        """Add a library root without replacing existing roots."""
        normalized = path.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {path}")
        if normalized in self._roots:
            return
        # A folder inside an existing root is one of that library's albums, not a
        # library of its own. Registering it separately put every album in the sidebar
        # on the same level as the library root (the "APPLE 层级不对" report).
        if any(self._is_within(normalized, root) for root in self._roots):
            LOGGER.info(
                "add_root: %s is inside an existing library root; treating it as an album",
                normalized,
            )
            return
        # Conversely, a new parent root subsumes roots nested inside it.
        self._roots = [root for root in self._roots if not self._is_within(root, normalized)]
        self._roots.append(normalized)
        self._sync_workspace_roots()
        self._refresh_tree()
        self._rebuild_watches()
        self.treeUpdated.emit()

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        """Return True when *path* is inside *parent* (and not equal to it)."""

        if path == parent:
            return False
        try:
            path.relative_to(parent)
        except ValueError:
            return False
        return True

    def remove_root(self, path: Path) -> None:
        """Remove a library root. If it was the primary root, promote the next one."""
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        def _matches(root: Path) -> bool:
            try:
                return root.resolve() == resolved
            except OSError:
                return root == resolved

        self._roots = [r for r in self._roots if not _matches(r)]
        self._sync_workspace_roots()
        self._refresh_tree()
        self._rebuild_watches()
        self.treeUpdated.emit()

    def invalidate_geotagged_assets_cache(self, *, emit_tree_updated: bool = False) -> None:
        """Drop cached map assets and optionally notify the UI to refresh views."""

        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        location_service = getattr(self, "location_service", None)
        invalidate_cache = getattr(location_service, "invalidate_cache", None)
        if callable(invalidate_cache):
            invalidate_cache()
        if emit_tree_updated:
            self.treeUpdated.emit()

    # ------------------------------------------------------------------
    # Binding and tree coordination
    # ------------------------------------------------------------------
    def bind_path(self, root: Path) -> None:
        self._bind_path(root, bind_session_if_needed=True)

    def bind_path_from_session(self, root: Path) -> None:
        """Bind *root* without creating a headless compatibility session."""

        self._bind_path(root, bind_session_if_needed=False)

    def _bind_path(self, root: Path, *, bind_session_if_needed: bool) -> None:
        LOGGER.debug("bind_path: binding to %s", root)
        # Clear existing watches to ensure initialization operations (like creating
        # the deleted items folder) do not trigger "directoryChanged" signals
        # from an active watcher, which would cause a double-refresh.
        self._clear_watches_for_rebind()

        # Cancel any in-flight scan so we do not block UI interactions while
        # rebinding to a new library root.
        self.stop_scanning()
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
        self._face_scan_status_message = None
        self._unbind_people_index_coordinator()

        normalized = root.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")
        if normalized not in self._roots:
            self._roots = [normalized]
        # Ensure the bound root is the primary root
        if self._roots and self._roots[0] != normalized:
            self._roots.remove(normalized)
            self._roots.insert(0, normalized)
        if bind_session_if_needed:
            self._bind_headless_session_if_needed(normalized)
        else:
            session = self._library_session
            if session is not None:
                try:
                    session_root = Path(session.library_root).resolve()
                except OSError:
                    session_root = Path(session.library_root)
                if session_root == normalized:
                    self.bind_people_service(session.people)
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        self._sync_workspace_roots()
        LOGGER.debug("bind_path: normalized root=%s", normalized)
        self._initialize_deleted_dir()
        self._refresh_tree()
        # If the album tree was unchanged, ``_refresh_tree()`` may have skipped
        # rebuilding the QFileSystemWatcher paths. Because ``bind_path()`` just
        # cleared all watcher directories, ensure we restore them so filesystem
        # monitoring is active even when binding an (initially) empty library.
        if not self._watcher.directories():
            LOGGER.debug(
                "bind_path: watcher has no directories after refresh; rebuilding watches"
            )
            self._rebuild_watches()
        # ``_refresh_tree()`` skips the ``treeUpdated`` emission when the album
        # list is unchanged (an optimisation for filesystem-watcher refreshes).
        # When binding a library for the first time the album list may be empty
        # both before and after the call, yet the UI model still needs to
        # transition from the "Bind Basic Library…" placeholder to the full
        # tree.  Emitting here only when the album list is empty preserves that
        # initial-model-rebuild behaviour without causing duplicate emissions
        # for non-empty libraries where ``_refresh_tree()`` has already emitted.
        if not self._albums:
            LOGGER.debug("bind_path: emitting treeUpdated for empty album tree")
            self.treeUpdated.emit()

    def _clear_watches_for_rebind(self) -> None:
        existing_dirs = self._watcher.directories()
        existing_files = self._watcher.files()
        if existing_dirs:
            self._watcher.removePaths(existing_dirs)
        if existing_files:
            self._watcher.removePaths(existing_files)
        if not self._watcher.directories() and not self._watcher.files():
            return

        try:
            self._watcher.directoryChanged.disconnect(self._on_directory_changed)
        except (RuntimeError, TypeError):
            pass
        self._watcher.deleteLater()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)

    def list_albums(self) -> list[AlbumNode]:
        return list(self._albums)

    def list_children(self, album: AlbumNode) -> list[AlbumNode]:
        return list(self._children.get(album.path, []))

    def scan_tree(self) -> list[AlbumNode]:
        self._refresh_tree()
        return self.list_albums()

    def shutdown(self) -> None:
        """Stop background workers and watchers during application shutdown."""

        self.stop_scanning()
        self._debounce.stop()
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._live_scan_buffer.clear()
        self._live_scan_root = None
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        if self._current_face_scanner is not None:
            self._current_face_scanner.cancel()
            self._current_face_scanner.wait(2000)
            if self._current_face_scanner.isRunning():
                LOGGER.warning(
                    "Face scan worker did not exit within 2 s after cancel(); "
                    "detaching without terminate() to avoid DB corruption."
                )
            self._current_face_scanner = None
        if self._current_ocr_scanner is not None:
            self._current_ocr_scanner.cancel()
            self._current_ocr_scanner = None
        self._unbind_people_index_coordinator()

    def face_scan_status_message(self) -> str | None:
        return self._face_scan_status_message


    def _bind_people_index_coordinator(self, root: Path) -> None:
        coordinator = get_people_index_coordinator(root)
        coordinator.resume()
        coordinator.snapshotCommitted.connect(
            self._on_people_snapshot_committed, Qt.ConnectionType.QueuedConnection
        )
        self._people_index_coordinator = coordinator

    def _bind_headless_session_if_needed(self, root: Path) -> None:
        session = self._library_session
        if session is not None and session.library_root == root:
            # ``bind_path()`` temporarily disconnects the People coordinator while
            # clearing state for a rebind. When a GUI-owned session is already
            # bound for this root, restore the People surface so snapshot events
            # keep flowing after the tree refresh completes.
            self.bind_people_service(session.people)
            return

        factory = self._session_factory
        if factory is None:
            LOGGER.debug(
                "No session factory bound; skipping headless session for %s",
                root,
            )
            return

        self.bind_library_session(
            factory(root),
            owned=True,
        )

    def _unbind_people_index_coordinator(self) -> None:
        if self._people_index_coordinator is None:
            return
        self._people_index_coordinator.begin_shutdown()
        try:
            self._people_index_coordinator.snapshotCommitted.disconnect(
                self._on_people_snapshot_committed
            )
        except (RuntimeError, TypeError):
            pass
        self._people_index_coordinator = None

    def _on_people_snapshot_committed(self, event: object) -> None:
        # ⭐ 防抖：使用定时器延迟发射信号，合并频繁更新
        # 根据视频播放状态动态调整防抖间隔
        if self._foreground_viewing_count > 0:
            self._people_update_debounce.setInterval(5000)  # 视频播放时5秒防抖
        else:
            self._people_update_debounce.setInterval(2000)  # 默认2秒防抖
        # 如果定时器已在运行，会重置计时（setSingleShot=True）
        self._people_update_debounce.start()
        # 保存事件以备延迟发射使用
        self._pending_people_event = event

    def _emit_people_update_deferred(self) -> None:
        """延迟发射人脸扫描更新信号（防抖回调）"""
        # 发射更新信号
        self.peopleIndexUpdated.emit()
        # 如果有待处理的事件，也发射快照信号
        if hasattr(self, '_pending_people_event'):
            self.peopleSnapshotCommitted.emit(self._pending_people_event)

    def register_foreground_viewing(self) -> None:
        """标注用户正在前台浏览（看照片或播视频），让后台扫描让路。

        人脸扫描与 OCR 都基于 onnxruntime 且要读整张原图，如果在用户看照片时
        继续跑，就会抢占 CPU/磁盘并让界面发涩。视频播放与照片浏览共用同一个
        计数，因此两者同时发生时计数依然正确。
        """

        self._foreground_viewing_count += 1
        if self._foreground_viewing_count == 1:
            self._people_update_debounce.setInterval(5000)
            self.set_video_playing(True)

    def unregister_foreground_viewing(self) -> None:
        """取消一次前台浏览登记；全部结束后恢复扫描。"""

        self._foreground_viewing_count = max(0, self._foreground_viewing_count - 1)
        if self._foreground_viewing_count == 0:
            self._people_update_debounce.setInterval(2000)
            self.set_video_playing(False)

    def register_video_playing(self) -> None:
        """视频播放的兼容入口。"""

        self.register_foreground_viewing()

    def unregister_video_playing(self) -> None:
        """视频播放的兼容入口。"""

        self.unregister_foreground_viewing()

    def set_video_playing(self, is_playing: bool) -> None:
        """⭐ 转发前台浏览状态到扫描协调器"""
        # 使用 ScanCoordinatorMixin 提供的方法
        try:
            super().set_video_playing(is_playing)
        except (AttributeError, TypeError):
            # 如果父类没有这个方法，忽略错误（向后兼容）
            pass

    # ------------------------------------------------------------------
    # Internal helpers (coordinator-level)
    # ------------------------------------------------------------------
    def _require_root(self) -> Path:
        root = self.root()
        if root is None:
            raise LibraryUnavailableError("Basic Library path has not been configured.")
        return root

    def _refresh_tree(self) -> None:
        if not self._roots:
            self._albums = []
            self._children = {}
            self._nodes = {}
            self._deleted_dir = None
            self._geotagged_assets_cache = None
            self._geotagged_assets_cache_root = None
            self._rebuild_watches()
            self.treeUpdated.emit()
            return
        previous_albums = self._albums
        previous_children = self._children
        previous_nodes = self._nodes
        albums: list[AlbumNode] = []
        children: Dict[Path, list[AlbumNode]] = {}
        new_nodes: Dict[Path, AlbumNode] = {}

        # Show each library root as a top-level album so the user sees all
        # bound libraries (e.g. APPLE, Screenshots) in the sidebar.
        for lib_root in self._roots:
            if not lib_root.exists():
                continue
            root_node = self._build_node(lib_root, level=1)
            albums.append(root_node)
            new_nodes[lib_root] = root_node
            child_nodes = [self._build_node(child, level=2) for child in self._iter_album_dirs(lib_root)]
            for child in child_nodes:
                new_nodes[child.path] = child
                # Sub-albums are one level deeper; without building them the tree
                # stopped at albums and sub-albums were invisible in the sidebar
                # even though they exist on disk.
                sub_nodes = [
                    self._build_node(sub, level=3)
                    for sub in self._iter_album_dirs(child.path)
                ]
                for sub in sub_nodes:
                    new_nodes[sub.path] = sub
                children[child.path] = sub_nodes
            children[lib_root] = child_nodes

        refreshed_albums = sorted(albums, key=lambda item: item.title.casefold())
        refreshed_children = {
            parent: sorted(kids, key=lambda item: item.title.casefold())
            for parent, kids in children.items()
        }
        if (
            new_nodes == previous_nodes
            and refreshed_albums == previous_albums
            and refreshed_children == previous_children
        ):
            return
        self._albums = refreshed_albums
        self._children = refreshed_children
        self._nodes = new_nodes
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        self._rebuild_watches()
        self.treeUpdated.emit()


__all__ = ["GeotaggedAsset", "LibraryRuntimeController"]
