"""The main window must actually provide every widget the coordinators wire up.

The coordinator used ``hasattr(ui, "widget")`` before every connection. Those
guards silently skipped wiring when a widget or signal was renamed — the feature
simply disappeared with no exception and no log.

The check is static on purpose: instantiating the real window inside the test
suite proved unstable (Qt aborts at teardown when combined with other tests), so
the contract is verified against the code that builds the window.
"""

from __future__ import annotations

import ast
from pathlib import Path

from iPhoto.gui.ui.ui_contract import WIRED_UI_ATTRS

_UI_MODULE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "iPhoto"
    / "gui"
    / "ui"
    / "ui_main_window.py"
)


def _assigned_ui_attributes() -> set[str]:
    """Return every ``self.<name>`` the window's ``setupUi`` assigns."""

    tree = ast.parse(_UI_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            names.add(node.attr)
    return names


def test_contract_matches_the_window_definition() -> None:
    assigned = _assigned_ui_attributes()

    missing = sorted(set(WIRED_UI_ATTRS) - assigned)

    assert missing == [], (
        "coordinators depend on these attributes but ui_main_window never "
        f"creates them: {missing}"
    )


def test_contract_has_no_duplicates() -> None:
    assert len(WIRED_UI_ATTRS) == len(set(WIRED_UI_ATTRS))
