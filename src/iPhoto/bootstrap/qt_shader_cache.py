"""Helpers for routing Qt and driver shader caches into managed work dirs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import MutableMapping

from ..config import WORK_DIR_NAME
from ..settings.manager import default_settings_path
from ..utils.pathutils import ensure_work_dir, get_custom_workspace_base


def load_saved_basic_library_path(settings_path: Path | None = None) -> Path | None:
    """Return the saved Basic Library root from ``settings.json`` when available."""

    try:
        resolved_settings_path = settings_path or default_settings_path()
        if not resolved_settings_path.exists():
            return None
        payload = json.loads(resolved_settings_path.read_text(encoding="utf-8"))
        raw_path = payload.get("basic_library_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        candidate = Path(raw_path).expanduser()
        return candidate if candidate.exists() else None
    except Exception:
        return None


def load_saved_workspace_base(settings_path: Path | None = None) -> Path | None:
    """Return ``workspace_base`` from ``settings.json`` when configured."""

    try:
        resolved_settings_path = settings_path or default_settings_path()
        if not resolved_settings_path.exists():
            return None
        payload = json.loads(resolved_settings_path.read_text(encoding="utf-8"))
        raw_path = payload.get("workspace_base")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        return Path(raw_path).expanduser()
    except Exception:
        return None


def resolve_managed_work_root(
    settings_path: Path | None = None,
    *,
    home_root: Path | None = None,
) -> Path:
    """Return the managed ``.iPhoto`` directory used for startup caches."""

    # The shader cache environment is configured before RuntimeContext exists, so
    # the workspace base has to be read from settings here instead of waiting for
    # RuntimeContext to install it. Without this the very first resolution fell
    # back to ``<library>/.iPhoto`` and a cache directory appeared in the photo
    # folder even though a custom workspace was configured.
    if get_custom_workspace_base() is None:
        saved_base = load_saved_workspace_base(settings_path)
        if saved_base is not None:
            from ..utils.pathutils import set_custom_workspace_base

            set_custom_workspace_base(saved_base)

    library_root = load_saved_basic_library_path(settings_path)
    if library_root is not None:
        work_dir = ensure_work_dir(library_root)
        if work_dir.exists():
            return work_dir

    # 检查是否设置了自定义工作目录基础路径
    custom_base = get_custom_workspace_base()
    if custom_base is not None:
        return custom_base

    user_home = home_root or Path.home()
    work_root = user_home / WORK_DIR_NAME
    work_root.mkdir(parents=True, exist_ok=True)
    return work_root


def resolve_shader_cache_root(
    settings_path: Path | None = None,
    *,
    home_root: Path | None = None,
) -> Path:
    """Return the managed shader cache directory."""

    cache_root = resolve_managed_work_root(settings_path, home_root=home_root) / "cache" / "shaders"
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


def configure_shader_cache_environment(
    settings_path: Path | None = None,
    *,
    home_root: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    """Configure process env vars so shader caches stay under ``.iPhoto``."""

    target_env = os.environ if environ is None else environ
    cache_root = resolve_shader_cache_root(settings_path, home_root=home_root)
    driver_cache_root = cache_root / "driver"
    qt3d_cache_root = cache_root / "qt3d"
    driver_cache_root.mkdir(parents=True, exist_ok=True)
    qt3d_cache_root.mkdir(parents=True, exist_ok=True)

    pipeline_cache_path = cache_root / "qt_rhi_pipeline.bin"

    target_env.setdefault("__GL_SHADER_DISK_CACHE", "1")
    target_env.setdefault("__GL_SHADER_DISK_CACHE_PATH", str(driver_cache_root))
    target_env.setdefault("QT3D_WRITABLE_CACHE_PATH", str(qt3d_cache_root))
    target_env.setdefault("QSG_RHI_PIPELINE_CACHE_SAVE", str(pipeline_cache_path))
    target_env.setdefault("QSG_RHI_PIPELINE_CACHE_LOAD", str(pipeline_cache_path))
    return cache_root


__all__ = [
    "configure_shader_cache_environment",
    "load_saved_basic_library_path",
    "resolve_managed_work_root",
    "resolve_shader_cache_root",
]
