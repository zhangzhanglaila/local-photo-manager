"""No module may read a global it never defines.

A botched rename left ``searchself._active_search_spec()`` and
``force_rebuild=force_rebuild`` inside nested worker methods in
``main_coordinator``. Both parse, both import, and both raise ``NameError`` the
moment the surrounding method runs — the embedding bootstrap died silently in a
``except Exception: logger.debug``. Import smoke tests cannot see this because
the broken lines only run later.

This test asks the compiler instead: for every module, each symbol that resolves
as a *global* must be assigned/imported at module level or be a builtin.
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "iPhoto"

EXTRA_GLOBALS = {
    "__name__",
    "__file__",
    "__doc__",
    "__package__",
    "__spec__",
    "__loader__",
    "__annotations__",
    "__builtins__",
    "print",  # some modules shadow builtins via locals(); harmless to allow
}


def _module_level_names(table: symtable.SymbolTable) -> set[str]:
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }


def _collect(
    table: symtable.SymbolTable,
    known: set[str],
    problems: list[tuple[str, int]],
) -> None:
    for symbol in table.get_symbols():
        if not symbol.is_global() or symbol.is_assigned():
            continue
        name = symbol.get_name()
        if name in known or hasattr(builtins, name):
            continue
        problems.append((name, table.get_lineno()))


def _undefined_globals(path: Path) -> list[tuple[str, int]]:
    source = path.read_text(encoding="utf-8")
    root = symtable.symtable(source, str(path), "exec")
    known = _module_level_names(root) | EXTRA_GLOBALS
    problems: list[tuple[str, int]] = []

    def walk(table: symtable.SymbolTable) -> None:
        _collect(table, known, problems)
        for child in table.get_children():
            walk(child)

    walk(root)
    return problems


def test_source_has_no_undefined_globals() -> None:
    problems = [
        f"{path.relative_to(SRC.parent.parent)}:{lineno}: {name}"
        for path in sorted(SRC.rglob("*.py"))
        for name, lineno in _undefined_globals(path)
    ]

    assert problems == [], "undefined globals (guaranteed NameError):\n" + "\n".join(problems)
