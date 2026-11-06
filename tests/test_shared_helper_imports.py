"""Every module that uses a shared helper must import it.

A batch refactor replaced inline logic with calls to shared helpers. In two files the
call site was rewritten but the import was not added, so the module imported fine and
only failed when the code path ran — one of them crashed the GUI at startup with
``NameError: name 'env_flag' is not defined``.

This test walks the source and verifies, statically, that each module which *uses*
one of the shared helpers also binds the name (by import, or by defining it).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

#: Names that are shared across modules and must be imported where used.
_SHARED_HELPERS = {
    "env_flag",
    "is_truthy",
    "diag",
    "paths_equal",
    "lonlat_to_normalized",
    "normalized_to_lonlat",
    "default_album_manifest",
    "normalise_album_manifest",
    "LibraryServiceRegistry",
    "SQLITE_BUSY_TIMEOUT_S",
    "SQLITE_LONG_BUSY_TIMEOUT_S",
    "MERCATOR_LAT_BOUND",
    "resolve_theme_mode",
    "set_current_mode",
}


def _module_files() -> list[Path]:
    return [
        path
        for root in (_SRC / "iPhoto", _SRC / "maps")
        if root.is_dir()
        for path in root.rglob("*.py")
    ]


def _bound_names(tree: ast.Module) -> set[str]:
    """Names the module binds itself: imports, assignments, class/function defs."""

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def _used_names(tree: ast.Module) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def test_shared_helpers_are_always_imported() -> None:
    problems: list[str] = []

    for path in _module_files():
        source = path.read_text(encoding="utf-8")
        if not any(helper in source for helper in _SHARED_HELPERS):
            continue
        tree = ast.parse(source)
        missing = (
            _SHARED_HELPERS & _used_names(tree)
        ) - _bound_names(tree)
        if missing:
            relative = path.relative_to(_SRC)
            problems.append(f"{relative}: uses {sorted(missing)} without importing")

    assert problems == [], "shared helpers used without an import:\n" + "\n".join(problems)


@pytest.mark.parametrize(
    "module",
    [
        "iPhoto.gui.render_backend",
        "iPhoto.gui.coordinators.main_coordinator",
        "iPhoto.gui.coordinators.edit_coordinator",
        "iPhoto.gui.coordinators.playback_coordinator",
    ],
)
def test_modules_import_cleanly(module: str) -> None:
    """Import smoke test for modules touched by the shared-helper refactor."""

    pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")
    import importlib

    importlib.import_module(module)
