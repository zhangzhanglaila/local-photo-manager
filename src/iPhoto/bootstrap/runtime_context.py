"""Formal runtime entry point for GUI startup and dependency wiring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt

from ..events.bus import EventBus

if TYPE_CHECKING:  # pragma: no cover
    from ..di.container import DependencyContainer
    from ..gui.facade import AppFacade
    from ..gui.ui.theme_manager import ThemeManager
    from ..i18n import LanguageStore
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
    from ..library.runtime_controller import LibraryRuntimeController
    from ..settings.manager import SettingsManager
    from .library_session import LibrarySession

_logger = logging.getLogger(__name__)


def _create_settings_manager() -> "SettingsManager":
    from ..settings.manager import SettingsManager

    manager = SettingsManager()
    manager.load()
    return manager


def _create_library_manager() -> "LibraryRuntimeController":
    from ..library.runtime_controller import LibraryRuntimeController

    return LibraryRuntimeController()


def _create_facade() -> "AppFacade":
    from ..gui.facade import AppFacade

    return AppFacade()


def _create_theme_manager(settings: "SettingsManager") -> "ThemeManager":
    from ..gui.ui.theme_manager import ThemeManager

    theme = ThemeManager(settings)
    theme.apply_theme()
    return theme


def _create_language_store(settings: "SettingsManager") -> "LanguageStore":
    from ..i18n import LanguageStore

    return LanguageStore(settings)


def _create_asset_runtime() -> "LibraryAssetRuntime":
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime

    return LibraryAssetRuntime()


def _create_event_bus() -> EventBus:
    return EventBus(logging.getLogger("EventBus"))


@dataclass
class RuntimeContext:
    """Authoritative runtime dependency bundle for GUI startup."""

    settings: "SettingsManager" = field(default_factory=_create_settings_manager)
    library: "LibraryRuntimeController" = field(default_factory=_create_library_manager)
    facade: "AppFacade" = field(default_factory=_create_facade)
    event_bus: EventBus = field(default_factory=_create_event_bus)
    asset_runtime: "LibraryAssetRuntime" = field(default_factory=_create_asset_runtime)
    recent_albums: list[Path] = field(default_factory=list)
    defer_startup_tasks: bool = False
    show_welcome_if_needed: bool = True  # 是否在需要时显示欢迎向导
    theme: "ThemeManager" = field(init=False)
    language: "LanguageStore" = field(init=False)
    library_session: "LibrarySession | None" = field(init=False, default=None)
    _container: "DependencyContainer | None" = field(
        init=False,
        default=None,
        repr=False,
    )
    _pending_basic_library_path: Path | None = field(init=False, default=None, repr=False)
    _pending_extra_roots: list[Path] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.theme = _create_theme_manager(self.settings)
        self.language = _create_language_store(self.settings)
        self.facade.bind_library(self.library)

        # 设置自定义工作目录基础路径
        workspace_base = self.settings.get("workspace_base")
        if workspace_base:
            from ..utils.pathutils import set_custom_workspace_base
            try:
                set_custom_workspace_base(Path(workspace_base).expanduser())
            except Exception as exc:
                _logger.warning("Failed to set custom workspace base: %s", exc)

        # Migrate: if basic_library_paths is empty but basic_library_path exists,
        # populate the list from the single path.
        stored_paths = self.settings.get("basic_library_paths") or []
        basic_path = self.settings.get("basic_library_path")
        if not stored_paths and isinstance(basic_path, str) and basic_path:
            stored_paths = [basic_path]
            self.settings.set("basic_library_paths", stored_paths)

        if stored_paths:
            self._pending_basic_library_path = Path(stored_paths[0]).expanduser()
            self._pending_extra_roots = [
                Path(p).expanduser() for p in stored_paths[1:]
            ]

        stored = self.settings.get("last_open_albums", []) or []
        resolved: list[Path] = []
        for entry in stored:
            try:
                resolved.append(Path(entry))
            except TypeError:
                continue
        if resolved:
            self.recent_albums = resolved[:10]

        if not self.defer_startup_tasks:
            self.resume_startup_tasks()

    def is_first_launch(self) -> bool:
        """检测是否首次启动或需要配置工作目录。

        首次启动的判断条件：
        1. 设置文件中没有任何工作目录配置
        2. 没有最近打开的相册记录
        """
        # 检查是否有工作目录配置
        has_workspace_config = self.settings.get("workspace_base") is not None

        # 检查是否有最近打开的相册
        has_recent_albums = bool(self.settings.get("last_open_albums", []))

        # 检查是否有配置的照片库路径
        has_library_paths = bool(self.settings.get("basic_library_paths", []))

        # 如果都没有配置，认为是首次启动
        return not (has_workspace_config or has_recent_albums or has_library_paths)

    def needs_workspace_config(self) -> bool:
        """检测是否需要配置工作目录。

        即使不是首次启动，如果没有配置 workspace_base，
        也应该询问用户选择工作目录位置。
        """
        has_workspace_config = self.settings.get("workspace_base") is not None
        return not has_workspace_config

    def show_welcome_wizard(self) -> Optional[Path]:
        """显示欢迎向导，返回用户选择的工作目录基础路径。

        Returns:
            用户选择的工作目录基础路径，如果选择传统模式则返回 None
        """
        try:
            from ..gui.ui.widgets import WelcomeWizard
            from PySide6.QtWidgets import QApplication

            _logger.info("show_welcome_wizard: Creating WelcomeWizard")

            # 获取主窗口作为父窗口
            app = QApplication.instance()
            parent = app.activeWindow() if app else None

            _logger.info("show_welcome_wizard: parent window = %s", parent)

            wizard = WelcomeWizard(parent)

            _logger.info("show_welcome_wizard: Showing wizard dialog")

            # 模态显示并等待用户选择
            workspace_base = wizard.show_and_wait()

            _logger.info("show_welcome_wizard: User selected workspace_base = %s", workspace_base)

            # 保存到设置
            if workspace_base is not None:
                self.settings.set("workspace_base", str(workspace_base))
            else:
                # 如果选择传统模式，确保 workspace_base 为 None
                self.settings.set("workspace_base", None)

            return workspace_base

        except Exception as exc:
            _logger.error("Failed to show welcome wizard: %s", exc)
            import traceback
            traceback.print_exc()
            return None

    @classmethod
    def create(cls, *, defer_startup: bool = False) -> "RuntimeContext":
        """Create a runtime context for desktop startup."""

        return cls(defer_startup_tasks=defer_startup)

    @property
    def container(self) -> "DependencyContainer":
        """Return an empty DI container for callers that still inspect it."""

        if self._container is None:
            from ..di.container import DependencyContainer

            self._container = DependencyContainer()
        return self._container

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work such as binding the default library path."""

        from ..config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
        from ..errors import LibraryError

        candidate = self._pending_basic_library_path
        extra_roots = list(self._pending_extra_roots)
        self._pending_basic_library_path = None
        self._pending_extra_roots = []
        if candidate is None:
            _logger.debug("resume_startup_tasks: no pending library path")
            return
        _logger.debug(
            "resume_startup_tasks: attempting to bind saved library path %s",
            candidate,
        )
        if candidate.exists():
            try:
                self.open_library(candidate)
                _logger.debug(
                    "resume_startup_tasks: bind_path succeeded, root=%s",
                    self.library.root(),
                )
                # 检查是否需要启动扫描
                # 增量扫描机制已经优化，即使数据库存在也会高效处理新文件
                # 所以每次启动都触发扫描以确保不遗漏任何新文件
                if not self.library.is_scanning_path(candidate):
                    from ..config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
                    _logger.info("resume_startup_tasks: starting scan to detect new files")
                    self.facade.scan_root_async(
                        candidate,
                        include=DEFAULT_INCLUDE,
                        exclude=DEFAULT_EXCLUDE,
                    )
            except LibraryError as exc:
                _logger.error("resume_startup_tasks: bind_path failed: %s", exc)
                self.library.errorRaised.emit(str(exc))
        else:
            _logger.warning(
                "resume_startup_tasks: saved path does not exist: %s",
                candidate,
            )
            self.library.errorRaised.emit(
                f"Basic Library path is unavailable: {candidate}"
            )

        # Bind additional library roots so they coexist with the primary.
        # Defer their scans until after the primary root scan finishes, because
        # the scan coordinator only supports one active scan at a time —
        # starting a new scan cancels the previous one.
        for extra in extra_roots:
            if not extra.exists():
                _logger.warning(
                    "resume_startup_tasks: extra root does not exist: %s", extra
                )
                continue
            try:
                self.library.add_root(extra)
                _logger.debug("resume_startup_tasks: added extra root %s", extra)
            except Exception as exc:
                _logger.error("resume_startup_tasks: add_root failed for %s: %s", extra, exc)
                self.library.errorRaised.emit(str(exc))

        if extra_roots:
            self._schedule_deferred_root_scans(extra_roots)

    def _schedule_deferred_root_scans(self, roots: list) -> None:
        """Scan extra roots sequentially after the primary scan finishes.

        The scan coordinator only supports one active scan at a time.  Starting
        a new scan cancels the previous one, so we must wait for each scan to
        complete before starting the next.
        """

        from ..config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE

        pending = list(roots)

        def _on_primary_scan_finished(_root: Path, _success: bool) -> None:
            # Disconnect after first trigger to avoid re-entrance.
            try:
                self.library.scanFinished.disconnect(_on_primary_scan_finished)
            except (RuntimeError, TypeError):
                pass
            _scan_next()

        def _scan_next() -> None:
            while pending:
                extra = pending.pop(0)
                if not extra.exists():
                    continue
                if self.library.is_scanning_path(extra):
                    continue
                _logger.info("resume_startup_tasks: scanning extra root %s", extra)
                try:
                    self.facade.scan_root_async(
                        extra,
                        include=DEFAULT_INCLUDE,
                        exclude=DEFAULT_EXCLUDE,
                    )
                except Exception as exc:
                    _logger.error("resume_startup_tasks: scan failed for %s: %s", extra, exc)
                    continue
                # Wait for this scan to finish before starting the next.
                def _on_extra_finished(finished_root: Path, _ok: bool, _extra=extra) -> None:
                    try:
                        self.library.scanFinished.disconnect(_on_extra_finished)
                    except (RuntimeError, TypeError):
                        pass
                    _scan_next()

                self.library.scanFinished.connect(_on_extra_finished)
                return

        # If the primary root is already scanning, wait for it; otherwise start immediately.
        primary = self.library.root()
        if primary and self.library.is_scanning_path(primary):
            self.library.scanFinished.connect(_on_primary_scan_finished)
        else:
            _scan_next()

    def open_library(self, root: Path) -> "LibrarySession":
        """Bind *root* as the active library and rebuild library-scoped adapters."""

        from .library_session import LibrarySession
        from ..errors import LibraryUnavailableError

        normalized = Path(root).expanduser().resolve()
        self.close_library()

        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")

        self.library_session = LibrarySession(
            normalized,
            asset_runtime=self.asset_runtime,
            bind_asset_runtime=False,
        )
        # ``LibraryRuntimeController.bind_library_session`` is the single binding
        # entry point and always exists. The old fallback rebound twelve services
        # one by one behind three layers of ``getattr`` probes; it could never run.
        self.library.bind_library_session(self.library_session)

        try:
            self.library.bind_path_from_session(normalized)
        except Exception:
            self.close_library()
            raise

        self.asset_runtime.bind_library_root(normalized)
        return self.library_session

    def close_library(self) -> None:
        """Close the active library-scoped session if one exists."""

        # One call unbinds every service bound by `bind_library_session`; the
        # per-service fallback below it was unreachable.
        self.library.bind_library_session(None)

        session = getattr(self, "library_session", None)
        if session is None:
            return
        session.shutdown()
        self.library_session = None

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list, keeping the most recent first."""

        normalized = root.resolve()
        self.recent_albums = [
            entry for entry in self.recent_albums if entry != normalized
        ]
        self.recent_albums.insert(0, normalized)
        del self.recent_albums[10:]
        self.settings.set(
            "last_open_albums",
            [str(path) for path in self.recent_albums],
        )


__all__ = ["RuntimeContext"]
