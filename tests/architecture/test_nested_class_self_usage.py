"""No nested worker class may read coordinator state through its own ``self``.

``SearchThread`` lives inside ``_handle_search_requested``, so its ``self`` is the
thread. It called ``self._active_search_spec()`` — a coordinator method — and the
resulting ``AttributeError`` was swallowed by a broad ``except`` that then reported
"AI 模型加载失败", which looked like a slow/broken model rather than a bug.

A nested class is a fresh scope: every attribute it reads off ``self`` must be one
it assigns, defines as a method, or declares at class level (signals).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "iPhoto"

#: Attributes Qt provides on every QRunnable/QThread, so they are always valid.
QT_BASE_ATTRS = {
    "setAutoDelete",
    "signals",
    "run",
    "wait",
    "start",
    "isInterruptionRequested",
    "deleteLater",
    "parent",
    "setObjectName",
    "objectName",
    "moveToThread",
    "thread",
    "connect",
    "emit",
    "isRunning",
    "requestInterruption",
    "quit",
    "exec",
    "finished",
}


def _class_level_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(statement.name)
    return names


def _self_attribute_uses(node: ast.ClassDef) -> tuple[set[str], list[str]]:
    assigned = _class_level_names(node)
    reads: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        if not isinstance(child.value, ast.Name) or child.value.id != "self":
            continue
        if isinstance(child.ctx, ast.Store):
            assigned.add(child.attr)
        else:
            reads.append(child.attr)
    return assigned, reads


def _nested_classes(tree: ast.Module) -> list[tuple[str, ast.ClassDef]]:
    """Return ``(enclosing function name, class)`` for classes defined in functions."""

    nested: list[tuple[str, ast.ClassDef]] = []
    for function in [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        for statement in function.body:
            if isinstance(statement, ast.ClassDef):
                nested.append((function.name, statement))
    return nested


def test_nested_worker_classes_only_use_their_own_state() -> None:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for owner, node in _nested_classes(tree):
            assigned, reads = _self_attribute_uses(node)
            unknown = sorted(
                {attr for attr in reads if attr not in assigned and attr not in QT_BASE_ATTRS}
            )
            if unknown:
                problems.append(
                    f"{path.relative_to(SRC.parent.parent)}:{node.lineno} "
                    f"{node.name} (in {owner}): {', '.join('self.' + name for name in unknown)}"
                )

    assert problems == [], (
        "nested classes reading state through their own self "
        "(capture the outer object instead):\n" + "\n".join(problems)
    )
