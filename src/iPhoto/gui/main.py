"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import ctypes
import faulthandler
import logging
import os
import signal
import sys
import threading
from pathlib import Path

faulthandler.enable()
# Periodically dump all thread tracebacks to stderr for debugging hangs.
# This is expensive and should only be enabled when investigating a hang.
if os.environ.get("IPHOTO_FAULT_DUMP_INTERVAL"):
    try:
        interval = int(os.environ["IPHOTO_FAULT_DUMP_INTERVAL"])
    except ValueError:
        interval = 300
    faulthandler.dump_traceback_later(timeout=interval, repeat=True, file=sys.stderr)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment
from iPhoto.gui.render_backend import should_configure_global_desktop_opengl
from iPhoto.i18n import tr

_logger = logging.getLogger(__name__)
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_MACOS_EXTERNAL_TOOL_PATHS = (
    Path("/opt/homebrew/bin"),
    Path("/opt/homebrew/sbin"),
    Path("/usr/local/bin"),
    Path("/usr/local/sbin"),
    Path("/opt/local/bin"),
    Path("/opt/local/sbin"),
)


def _bootstrap_macos_external_tool_path() -> None:
    """Expose common Homebrew/MacPorts tool paths to GUI-launched app bundles."""

    if sys.platform != "darwin":
        return

    # Use the target platform's PATH separator rather than the host process
    # separator so darwin-specific normalization also behaves correctly in
    # cross-platform tests that monkeypatch ``sys.platform``.
    path_separator = ":"

    existing_tool_paths: list[str] = []
    for candidate in _MACOS_EXTERNAL_TOOL_PATHS:
        try:
            if candidate.is_dir():
                existing_tool_paths.append(candidate.as_posix())
        except OSError:
            continue

    current_paths = [
        entry
        for entry in os.environ.get("PATH", "").split(path_separator)
        if entry
    ]
    merged_paths: list[str] = []
    seen: set[str] = set()
    for entry in [*existing_tool_paths, *current_paths]:
        if entry in seen:
            continue
        seen.add(entry)
        merged_paths.append(entry)
    if merged_paths:
        os.environ["PATH"] = path_separator.join(merged_paths)


def _configure_qt_shader_disk_cache() -> None:
    """Route shader/program caches into a managed ``.iPhoto`` work directory."""
    configure_shader_cache_environment()


def _opengl_explicitly_disabled() -> bool:
    """Return whether all OpenGL-backed UI surfaces should be disabled."""

    return os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in _TRUE_ENV_VALUES


def _map_gl_surface_format(platform: str | None = None) -> QSurfaceFormat:
    """Return the conservative OpenGL surface format used by map widgets."""

    platform = sys.platform if platform is None else platform
    surface_format = QSurfaceFormat()
    surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    surface_format.setDepthBufferSize(24)
    surface_format.setStencilBufferSize(8)
    surface_format.setAlphaBufferSize(8 if platform == "darwin" else 0)
    surface_format.setSamples(0)
    return surface_format


def _is_packaged_runtime() -> bool:
    """Return ``True`` when the app is running from a compiled/frozen bundle."""

    return "__compiled__" in globals() or getattr(sys, "frozen", False)


def _allow_packaged_linux_wayland() -> bool:
    """Return whether packaged Linux builds may keep Qt's default platform selection."""

    raw_value = os.environ.get("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", "").strip().lower()
    return raw_value in _TRUE_ENV_VALUES


def _prefer_local_source_tree() -> None:
    """Ensure direct script runs import the workspace package first.

    When ``main.py`` is launched directly from an IDE, Python may resolve the
    editable ``iPhoto`` install from another checkout before this repo's
    ``src`` tree. Prepending the local ``src`` path keeps the GUI aligned with
    the code being edited.
    """

    src_root = Path(__file__).resolve().parents[2]
    src_root_str = str(src_root)
    if sys.path and sys.path[0] == src_root_str:
        return
    try:
        sys.path.remove(src_root_str)
    except ValueError:
        pass
    sys.path.insert(0, src_root_str)


def _prepare_qt_runtime_for_maps() -> None:
    """Apply Linux Qt platform flags required by the native OsmAnd widget.

    ``PhotoMapView`` prefers the native OsmAnd widget when its runtime is
    available. That widget expects Qt to use the XCB/GLX desktop OpenGL path on
    Linux; without these flags the application can start successfully and only
    fail later when the map view is opened with GLEW reporting missing GLX
    support.
    """

    if sys.platform != "linux":
        return

    if _opengl_explicitly_disabled():
        return

    if _is_packaged_runtime():
        if _allow_packaged_linux_wayland():
            return
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    else:
        try:
            from maps.map_sources import has_usable_osmand_native_widget, prefer_osmand_native_widget
        except Exception:
            return

        maps_package_root = Path(__file__).resolve().parents[2] / "maps"
        if not prefer_osmand_native_widget() or not has_usable_osmand_native_widget(maps_package_root):
            return

    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    if os.environ.get("QT_QPA_PLATFORM") == "xcb":
        os.environ.setdefault("QT_OPENGL", "desktop")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "xcb_glx")


def _configure_qt_opengl_defaults() -> None:
    """Apply OpenGL context defaults required by the map widgets."""

    _configure_qt_shader_disk_cache()

    if _opengl_explicitly_disabled():
        return

    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception:
        pass

    if should_configure_global_desktop_opengl():
        try:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
        except Exception:
            pass

    try:
        QSurfaceFormat.setDefaultFormat(_map_gl_surface_format())
    except Exception:
        return


def _install_global_exception_hook() -> None:
    """Log unhandled exceptions that would otherwise crash silently.

    PySide6 calls ``sys.excepthook`` when a Python exception propagates out of
    a C++-invoked slot.  Some PySide6 versions then call ``sys.exit(1)``.
    We override the hook so that exceptions are *logged only* and never trigger
    a process exit — the application continues running.
    """

    import traceback

    _hook_logger = logging.getLogger("iPhoto.crash")

    def _excepthook(exc_type, exc_value, exc_tb):
        _hook_logger.critical(
            "Unhandled exception (app continues):\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        # Write to crash log file as well (survives process death).
        # 尝试使用自定义工作目录，回退到用户目录
        from ..utils.pathutils import get_custom_workspace_base
        custom_base = get_custom_workspace_base()
        if custom_base is not None:
            crash_path = custom_base / "crash.log"
        else:
            crash_path = Path.home() / ".iPhoto" / "crash.log"
        try:
            crash_path.parent.mkdir(parents=True, exist_ok=True)
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write("=== Unhandled exception ===\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                f.write("\n")
        except OSError:
            pass
        # Do NOT call sys.__excepthook__ — some PySide6 versions call
        # sys.exit(1) after excepthook returns.

    sys.excepthook = _excepthook
    # Also replace the internal reference so PySide6's PyErr_Print path
    # uses our hook instead of the default one.
    sys.__excepthook__ = _excepthook

    # Qt swallows exceptions in slots/signals — install a handler via
    # QThreadPool's exception handling or threading.
    import threading
    _orig_thread_excepthook = threading.excepthook

    def _thread_excepthook(args):
        _hook_logger.critical(
            "Unhandled exception in thread %s:\n%s",
            args.thread,
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb)),
        )
        # Do NOT call original thread excepthook (which may exit).

    threading.excepthook = _thread_excepthook


def main(argv: list[str] | None = None) -> int:
    """Launch the Qt application and return the exit code."""

    _install_global_exception_hook()
    _prefer_local_source_tree()
    _bootstrap_macos_external_tool_path()
    maps_package_root = Path(__file__).resolve().parents[2] / "maps"
    try:
        from maps.map_sources import apply_pending_osmand_extension_install

        apply_pending_osmand_extension_install(maps_package_root)
    except Exception:
        _logger.warning("Failed to apply pending map extension install", exc_info=True)

    # Ensure the ``iPhoto`` root logger is configured before any component
    # creates a child logger.  ``get_logger()`` lazily attaches a StreamHandler
    # to the ``iPhoto`` logger so all ``iPhoto.*`` loggers propagate output to
    # stderr at INFO level by default.
    from iPhoto.utils.logging import get_logger as _init_logging
    _init_logging()

    arguments = list(sys.argv if argv is None else argv)
    _prepare_qt_runtime_for_maps()
    _configure_qt_opengl_defaults()
    # Suppress noisy Qt RHI/OpenGL debug output (e.g. the full GL extension
    # list printed by NVIDIA drivers) that clutters the terminal on startup.
    # Use explicit assignment (not setdefault) so it always takes effect.
    os.environ["QT_LOGGING_RULES"] = (
        "qt.rhi*=false;qt.gui*=false;"
        "qt.qpa.opengl*=false;qt.opengl*=false"
    )
    app = QApplication(arguments)

    # Allow Ctrl+C to terminate the app on Windows, even when the UI thread
    # is completely frozen in native code.  Python's signal handler cannot run
    # when the main thread is blocked inside Qt, so we register a Windows
    # Console Control Handler (runs in a dedicated OS thread) that directly
    # terminates the process via TerminateProcess.
    if sys.platform == "win32":
        _ctrl_c_event = threading.Event()

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _console_ctrl_handler(ctrl_type: int) -> int:
            if ctrl_type in (0, 1):  # CTRL_C_EVENT, CTRL_BREAK_EVENT
                _logger.info("Console Ctrl+C detected, force exiting")
                _ctrl_c_event.set()
                return 1
            return 0

        try:
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ctrl_handler, 1)
        except Exception:
            pass

        def _watchdog() -> None:
            _ctrl_c_event.wait()
            os._exit(130)

        _watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        _watchdog_thread.start()

    # ``QToolTip`` instances inherit ``WA_TranslucentBackground`` from the frameless
    # main window, which means they expect the application to provide an opaque fill
    # colour.  Some Qt styles ignore stylesheet rules for tooltips, so we proactively
    # update the palette that drives those popups to guarantee readable text.
    tooltip_palette = QPalette(app.palette())

    def _resolved_colour(source: QColor, fallback: QColor) -> QColor:
        """Return a copy of *source* with a fully opaque alpha channel.

        Qt reports transparent colours for certain palette roles when
        ``WA_TranslucentBackground`` is active.  Failing to normalise the alpha value
        causes the compositor to blend the tooltip against the desktop wallpaper,
        producing the solid black rectangle described in the regression report.
        Falling back to a well-tested default keeps the tooltip legible even on
        themes that omit one of the roles we query.
        """

        if not source.isValid():
            return QColor(fallback)

        resolved = QColor(source)
        resolved.setAlpha(255)
        return resolved

    base_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.Window), QColor("#eef3f6")
    )
    text_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.WindowText), QColor(Qt.GlobalColor.black)
    )

    # Ensure the text remains readable by checking the lightness contrast.  When the
    # palette provides nearly identical shades we fall back to a simple dark-on-light
    # scheme that mirrors Qt's built-in defaults.
    if abs(base_colour.lightness() - text_colour.lightness()) < 40:
        base_colour = QColor("#eef3f6")
        text_colour = QColor(Qt.GlobalColor.black)

    tooltip_palette.setColor(QPalette.ColorRole.ToolTipBase, base_colour)
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipText, text_colour)
    app.setPalette(tooltip_palette, "QToolTip")

    from iPhoto.bootstrap.runtime_context import RuntimeContext
    from iPhoto.gui.coordinators.main_coordinator import MainCoordinator
    from iPhoto.gui.ui.main_window import MainWindow

    # Defer heavy library binding + initial scan until the event loop is running.
    context = RuntimeContext.create(defer_startup=True)
    # --- Phase 4: Coordinator Wiring ---
    window = MainWindow(context)

    # Startup overlay — shown immediately so the user sees progress feedback.
    from iPhoto.gui.ui.widgets.startup_overlay import StartupOverlay
    overlay = StartupOverlay(window.ui.window_shell)

    def _set_startup_placeholder(active: bool) -> None:
        """Let the overlay, or the gallery, own the startup feedback.

        Both used to render at once, so the window showed "正在打开图库…" and a
        separate "准备中..." box beside it.
        """

        view = getattr(getattr(window.ui, "gallery_page", None), "grid_view", None)
        setter = getattr(view, "set_startup_overlay_active", None)
        if callable(setter):
            setter(active)

    # Coordinator needs Window, Context, and Container
    overlay.show_overlay()
    _set_startup_placeholder(True)
    window.show()

    # Break the heavy startup into multiple event-loop steps so the overlay
    # can repaint between each step (prevents "未响应" appearance).
    # ``processEvents()`` is called after each ``set_message`` to let the
    # overlay repaint before the next heavy synchronous operation begins.
    coordinator_ref: list = []
    _scan_finished = False  # 跟踪扫描是否完成
    _step5_executed = False  # 防止 _step5_select_photos 被重复调用导致 overlay 二次访问

    # ⭐ 关键修改：先连接信号，再启动扫描，避免信号丢失
    def _connect_scan_signals() -> None:
        """在启动任何扫描之前连接信号。"""
        nonlocal _scan_finished

        def _on_scan_progress(root: Path, current: int, total: int) -> None:
            nonlocal _scan_finished
            if not _scan_finished:
                overlay.show_scan_progress(current, total)
                app.processEvents()

        def _on_scan_finished(root: Path, success: bool) -> None:
            nonlocal _scan_finished
            if _scan_finished:
                return  # signal may fire from multiple sources
            _scan_finished = True
            _logger.info("startup: scan finished, success=%s", success)
            # 扫描完成后继续下一步
            QTimer.singleShot(0, _step5_select_photos)

        # 连接信号
        context.facade.scanProgress.connect(_on_scan_progress)
        context.library.scanProgress.connect(_on_scan_progress)
        context.facade.scanFinished.connect(_on_scan_finished)
        context.library.scanFinished.connect(_on_scan_finished)

    def _check_existing_db() -> bool:
        """检查是否已有数据库。"""
        from iPhoto.utils.pathutils import resolve_work_dir, get_custom_workspace_dir

        lib_root = context.library.root()
        if not lib_root:
            return False

        # 先尝试自定义工作目录
        custom_dir = get_custom_workspace_dir(lib_root)
        if custom_dir is not None:
            custom_db = custom_dir / "global_index.db"
            if custom_db.exists():
                _logger.info("startup: found DB in custom workspace: %s", custom_db)
                return True

        # 回退到传统工作目录检测
        work_dir = resolve_work_dir(lib_root)
        if work_dir is not None:
            db_path = work_dir / "global_index.db"
            if db_path.exists():
                _logger.info("startup: found DB in traditional workspace: %s", db_path)
                return True

        return False

    def _step1_create_coordinator() -> None:
        try:
            overlay.set_message(tr("startup.init_components"))
            app.processEvents()
            _logger.info("startup step 1: creating MainCoordinator")
            coordinator = MainCoordinator(window, context)
            window.set_coordinator(coordinator)
            coordinator_ref.append(coordinator)
            QTimer.singleShot(0, _step2_connect_signals_first)
        except Exception:
            _logger.exception("startup step 1: unhandled error")
            raise

    def _step2_connect_signals_first() -> None:
        """⚠️ 重要：在 resume_startup_tasks 之前连接扫描信号。"""
        try:
            overlay.set_message("初始化信号...")
            app.processEvents()
            _logger.info("startup step 2: connecting scan signals BEFORE opening library")

            # 先连接 overlay 的扫描信号
            _connect_scan_signals()

            # ⭐ 同时连接状态栏的扫描信号
            if coordinator_ref:
                coordinator = coordinator_ref[0]

                def _on_status_bar_scan_progress(root: Path, current: int, total: int) -> None:
                    nonlocal _scan_finished
                    if not _scan_finished:
                        # 确保状态栏显示扫描进度
                        coordinator._status_bar.handle_scan_progress(root, current, total)

                def _on_status_bar_scan_finished(root: Path, success: bool) -> None:
                    coordinator._status_bar.handle_scan_finished(root, success)

                # 连接 facade 的扫描信号到状态栏
                context.facade.scanProgress.connect(_on_status_bar_scan_progress)
                context.facade.scanFinished.connect(_on_status_bar_scan_finished)
                context.library.scanProgress.connect(_on_status_bar_scan_progress)
                context.library.scanFinished.connect(_on_status_bar_scan_finished)

            QTimer.singleShot(0, _step3_check_first_launch)
        except Exception:
            _logger.exception("startup step 2: unhandled error")
            raise

    def _step3_check_first_launch() -> None:
        """检查是否首次启动。"""
        try:
            overlay.set_message("检查配置...")
            app.processEvents()
            _logger.info("startup step 3: checking first launch")
            if context.needs_workspace_config():
                overlay.set_message("配置工作目录...")
                app.processEvents()
                _logger.info("startup step 3: workspace configuration needed")
                context.show_welcome_wizard()
            QTimer.singleShot(0, _step4_open_library)
        except Exception:
            _logger.exception("startup step 3: unhandled error")
            raise

    def _step4_open_library() -> None:
        """打开库并决定是否等待扫描。"""
        try:
            nonlocal _scan_finished

            overlay.set_message(tr("startup.opening_library"))
            app.processEvents()
            _logger.info("startup step 4: opening library")

            # 检查是否已有数据库
            has_existing_db = _check_existing_db()

            # 打开库（可能启动扫描）
            context.resume_startup_tasks()

            if has_existing_db:
                # 数据库已存在，不需要等待扫描
                _scan_finished = True
                _logger.info("startup: existing DB found, will not wait for scan")
                if not _step5_executed:
                    QTimer.singleShot(0, _step5_select_photos)
            else:
                # 需要等待扫描完成，设置超时
                _logger.info("startup: no DB found, waiting for scan")
                QTimer.singleShot(5000, _check_scan_timeout)
        except Exception:
            _logger.exception("startup step 4: unhandled error")
            raise

    def _check_scan_timeout() -> None:
        """检查扫描是否超时（5秒后）。"""
        nonlocal _scan_finished
        if _step5_executed:
            return  # _step5 已经执行过（如扫描完成后被触发），不要再重复触发
        if not _scan_finished:
            _logger.warning("startup: scan timeout, continuing anyway")
            _scan_finished = True
            QTimer.singleShot(0, _step5_select_photos)

    def _warm_up_thumbnail_cache() -> None:
        """在后台预热缩略图缓存以改善首次浏览体验。"""
        try:
            coordinator = coordinator_ref[0]
            gallery_store = getattr(coordinator, "_gallery_store", None)
            if gallery_store is None:
                return

            # ⭐ 修复：使用 count() 而不是 rowCount()
            preload_count = min(30, gallery_store.count())
            if preload_count <= 0:
                return

            _logger.info("thumbnail cache warm-up: triggering preload for %d assets", preload_count)

            # 触发缩略图服务的预加载
            # 通过访问每项的缩略图来触发缓存生成
            from PySide6.QtCore import QTimer, QSize

            def _preload_batch(offset: int = 0) -> None:
                batch_size = 5
                end = min(offset + batch_size, preload_count)
                for row in range(offset, end):
                    asset = gallery_store.asset_at(row)
                    if asset is not None and asset.abs_path is not None:
                        try:
                            # 通过 thumbnail_service 触发缓存预热
                            coordinator._thumbnail_service.get_thumbnail(
                                path=asset.abs_path,
                                size=QSize(256, 256),
                            )
                        except Exception:
                            pass  # 忽略预加载错误

                # 继续下一批
                if end < preload_count:
                    QTimer.singleShot(50, lambda: _preload_batch(end))

            # 启动批量预加载
            _preload_batch()
            _logger.info("thumbnail cache warm-up initiated")

        except Exception:
            _logger.exception("thumbnail cache warm-up failed")

    def _step5_select_photos() -> None:
        """选择照片并完成启动。"""
        nonlocal _step5_executed
        try:
            # ⭐ 防重入：_step5 可能从 _on_scan_finished / _step4 / _check_scan_timeout
            # 三条路径触发，必须保证只执行一次，否则会再次访问已删除的 overlay。
            if _step5_executed:
                _logger.info("startup step 5: already executed, skipping")
                return
            _step5_executed = True

            # 确保扫描已完成后再继续
            if not _scan_finished:
                _step5_executed = False  # 允许下次重新执行
                _logger.info("startup step 5: scan still in progress, waiting...")
                QTimer.singleShot(100, _step5_select_photos)
                return

            # ⭐ 检查 overlay 是否仍然有效
            if overlay is None or not hasattr(overlay, "_label"):
                _logger.warning("startup step 5: overlay already deleted, skipping")
                coordinator = coordinator_ref[0]
                coordinator.finish_startup()
                return

            overlay.set_message(tr("startup.loading_photos"))
            app.processEvents()
            coordinator = coordinator_ref[0]

            # 检查是否有有效的相册路径参数
            valid_path_arg = None
            if len(arguments) > 1:
                arg = arguments[1]
                if arg not in ("--help", "-h", "--version") and not arg.startswith("-"):
                    potential_path = Path(arg)
                    if potential_path.exists():
                        valid_path_arg = potential_path

            # ⭐ 检查是否已绑定图库
            library_root = context.library.root()
            if library_root is None:
                # 没有绑定图库，显示友好提示
                _logger.info("startup step 5: no library bound, showing empty state")
                # 设置空状态提示
                _set_startup_placeholder(False)
                grid_view = getattr(window.ui.gallery_page, "grid_view", None)
                if grid_view and hasattr(grid_view, "set_empty_mode"):
                    grid_view.set_empty_mode("no_library")
                coordinator.finish_startup()
                overlay.dismiss()
                _logger.info("startup complete (no library)")
                return

            if valid_path_arg:
                _logger.info("startup step 5: opening album from CLI argument %s", valid_path_arg)
                coordinator.open_album_from_path(valid_path_arg)
            else:
                _logger.info("startup step 5: selecting All Photos")
                _logger.info("[DIAG] _step5: library_root=%s", library_root)
                _logger.info("[DIAG] _step5: store.count before open_all_photos=%s", coordinator._gallery_store.count() if hasattr(coordinator, "_gallery_store") else "no_store")
                _logger.info("[DIAG] _step5: asset_query_service=%s", coordinator._asset_query_service() if hasattr(coordinator, "_asset_query_service") else "no_method")
                # ⭐ 关键修复：直接调用 navigation_coordinator.open_all_photos()
                # 而不是通过 sidebar 信号链（sidebar signal chain 可能在 sidebar model 还没 refresh 时失效）
                coordinator._navigation.open_all_photos()
                # ⭐ 关键修复：强制刷新 sidebar model，确保 "All Photos" 节点存在
                # 否则 select_all_photos 会因为 index 无效而失败
                try:
                    sidebar_model = window.ui.sidebar._model
                    if sidebar_model is not None:
                        _logger.info("[DIAG] _step5: forcing sidebar model refresh before select_all_photos")
                        sidebar_model.refresh()
                except Exception:
                    _logger.debug("sidebar model refresh failed", exc_info=True)
                # 仍然尝试更新 sidebar UI 选择状态（不影响加载逻辑）
                try:
                    window.ui.sidebar.select_all_photos(emit_signal=False)
                except Exception:
                    _logger.debug("sidebar select_all_photos failed (non-critical)", exc_info=True)
                _logger.info("[DIAG] _step5: store.count after open_all_photos=%s", coordinator._gallery_store.count() if hasattr(coordinator, "_gallery_store") else "no_store")

            coordinator.finish_startup()

            # ⭐ 关键修复：清除所有加载/扫描图层
            # grid view 在 setModel 时设置了 _query_loading=True，
            # 而 _on_loading_timeout 需要 2s 才触发，期间用户会看到"加载中"。
            # 显式调用 dismiss_loading_state 立即清除这些图层。
            gallery_page = getattr(window.ui, "gallery_page", None)
            if gallery_page:
                _set_startup_placeholder(False)
                grid_view = getattr(gallery_page, "grid_view", None)
                if grid_view and hasattr(grid_view, "dismiss_loading_state"):
                    grid_view.dismiss_loading_state()
                # 同时强制 stack 切换到 grid_view，避免 _loading_widget 残留
                if hasattr(gallery_page, "_stack") and hasattr(gallery_page, "grid_view"):
                    if gallery_page._stack.currentWidget() is not gallery_page.grid_view:
                        gallery_page._stack.setCurrentWidget(gallery_page.grid_view)

            # ⭐ 启动缓存预热：在后台预加载可见区域的缩略图
            # 这可以显著改善首次浏览时的流畅度
            QTimer.singleShot(500, _warm_up_thumbnail_cache)

            overlay.dismiss()
            _logger.info("startup complete")
        except Exception:
            _logger.exception("startup step 5: unhandled error")
            raise

    QTimer.singleShot(0, _step1_create_coordinator)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
