"""Startup caches must respect the configured workspace.

The Qt shader cache environment is configured in ``main()`` before
``RuntimeContext`` exists, so the workspace base is not installed yet. The path
resolution therefore used to fall back to ``<library>/.iPhoto`` and created a
cache directory inside the photo folder, even though a custom workspace was
configured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iPhoto.bootstrap.qt_shader_cache import (
    load_saved_workspace_base,
    resolve_managed_work_root,
    resolve_shader_cache_root,
)
from iPhoto.utils.pathutils import set_custom_workspace_base


@pytest.fixture(autouse=True)
def _reset_workspace() -> None:
    set_custom_workspace_base(None)
    yield
    set_custom_workspace_base(None)


def _settings(tmp_path: Path, library: Path, workspace: Path | None) -> Path:
    payload: dict[str, object] = {"basic_library_path": str(library)}
    if workspace is not None:
        payload["workspace_base"] = str(workspace)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_shader_cache_uses_the_configured_workspace(tmp_path: Path) -> None:
    library = tmp_path / "APPLE"
    library.mkdir()
    workspace = tmp_path / "Workspace"
    settings = _settings(tmp_path, library, workspace)

    # Nothing installed the workspace base yet: this is the startup situation.
    work_root = resolve_managed_work_root(settings)

    assert work_root == workspace / "APPLE" / ".iPhoto"
    assert not (library / ".iPhoto").exists(), (
        "the photo folder must not receive a work directory when a workspace is configured"
    )


def test_shader_cache_falls_back_into_the_library(tmp_path: Path) -> None:
    library = tmp_path / "APPLE"
    library.mkdir()
    settings = _settings(tmp_path, library, None)

    work_root = resolve_managed_work_root(settings)

    assert work_root == library / ".iPhoto"


def test_saved_workspace_base_is_read(tmp_path: Path) -> None:
    library = tmp_path / "APPLE"
    library.mkdir()
    workspace = tmp_path / "Workspace"
    settings = _settings(tmp_path, library, workspace)

    assert load_saved_workspace_base(settings) == workspace
    assert load_saved_workspace_base(tmp_path / "missing.json") is None


def test_shader_cache_root_lives_under_the_workspace(tmp_path: Path) -> None:
    library = tmp_path / "APPLE"
    library.mkdir()
    workspace = tmp_path / "Workspace"
    settings = _settings(tmp_path, library, workspace)

    cache_root = resolve_shader_cache_root(settings)

    assert cache_root == workspace / "APPLE" / ".iPhoto" / "cache" / "shaders"
    assert cache_root.exists()
    assert not (library / ".iPhoto").exists()
