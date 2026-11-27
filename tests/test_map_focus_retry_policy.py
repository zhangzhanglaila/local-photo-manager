"""One implementation per behaviour: the map-focus retry must not be duplicated.

``MainCoordinator`` (now ``LocationCoordinator``) defined ``_try_focus_map`` and
``_retry_focus`` twice, plus
``_FOCUS_MAP_MAX_RETRIES`` twice (30 then 10). Python keeps the last definition,
so the first pair was dead code with *different* behaviour (100 ms, and it kept
retrying even when the map view was missing). A silent override like that hides
which policy actually runs.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.gui.coordinators import location_coordinator as module
from iPhoto.gui.coordinators.location_coordinator import LocationCoordinator


def _class_definitions(name: str) -> list[ast.FunctionDef]:
    tree = ast.parse(inspect.getsource(LocationCoordinator))
    class_node = tree.body[0]
    assert isinstance(class_node, ast.ClassDef)
    return [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]


def test_each_retry_helper_is_defined_once() -> None:
    assert len(_class_definitions("_try_focus_map")) == 1
    assert len(_class_definitions("_retry_focus")) == 1


def test_retry_policy_lives_in_one_place() -> None:
    source = inspect.getsource(LocationCoordinator)

    assert "_FOCUS_MAP_MAX_RETRIES" not in source, "the class-level duplicate must stay gone"
    assert module.MAP_FOCUS_MAX_RETRIES == 10
    assert module.MAP_FOCUS_RETRY_INTERVAL_MS == 400


def test_missing_map_view_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing map view is a broken contract, not a state to retry.

    The old guard (``if not hasattr(ui, "map_view") ...: return``) could never be
    true — ``setupUi`` always builds the map page — so it only hid the bug.
    """

    scheduled: list[int] = []
    monkeypatch.setattr(
        module.QTimer, "singleShot", lambda ms, cb: scheduled.append(ms)
    )

    # A plain stub keeps Qt out of it: the method only reads ``self._window`` and
    # schedules through ``self._retry_focus``.
    coordinator = SimpleNamespace(_window=SimpleNamespace(ui=SimpleNamespace()))

    with pytest.raises(AttributeError):
        LocationCoordinator._try_focus_map(coordinator, 30.0, 120.0, 0)

    assert scheduled == [], "no retries are scheduled for a broken contract"
