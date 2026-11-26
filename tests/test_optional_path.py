"""Path validation replaced the production code's ``unittest.mock`` sniffing.

Four GUI services used to decide "is this collaborator real?" by inspecting
``candidate.__class__.__module__``. That made production code depend on how tests
build doubles, and it did not help when a *real* object simply had no path yet.
The replacement validates the value instead, so these tests pin both directions:
real paths survive, doubles that answer every attribute with another double turn
into "no path".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.gui.services.people_service_resolver import bound_people_service
from iPhoto.utils.pathutils import optional_path


def test_optional_path_accepts_paths_and_strings(tmp_path: Path) -> None:
    assert optional_path(tmp_path) == tmp_path
    assert optional_path(str(tmp_path)) == tmp_path


def test_optional_path_rejects_absent_and_non_path_values() -> None:
    assert optional_path(None) is None
    assert optional_path(object()) is None
    assert optional_path(42) is None


def test_unconfigured_double_resolves_to_no_path() -> None:
    """A double's ``library_root()`` returns another double; that is not a path."""

    double = Mock()
    assert optional_path(double.library_root()) is None


def test_people_resolver_ignores_a_manager_holding_a_double(tmp_path: Path) -> None:
    manager = Mock()
    manager.people_service = Mock()

    assert bound_people_service(manager, library_root=tmp_path) is None
    assert bound_people_service(manager) is manager.people_service
