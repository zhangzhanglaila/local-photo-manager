"""The gallery must never silently drop its visible-range signal.

``_emit_visible_rows_impl`` used to catch every exception and return. When
``rowCount()`` raised, the visible range was never published, so the windowed
model never loaded anything and the gallery sat on its loading state forever —
with no log line to explain it.

The tests drive the two methods directly with a stubbed model: attaching a model
that raises would also make Qt's own layout code raise from a C++ callback.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.asset_grid import AssetGrid


class _RaisingModel:
    """Stand-in model whose ``rowCount`` raises the configured exception."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def rowCount(self) -> int:  # noqa: N802 - mirrors the Qt API
        raise self._error


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _grid(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> AssetGrid:
    grid = AssetGrid()
    monkeypatch.setattr(AssetGrid, "model", lambda self: _RaisingModel(error))
    return grid


def test_torn_down_view_is_tolerated(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid = _grid(monkeypatch, RuntimeError("Internal C++ object already deleted"))

    # A destroyed model during teardown must not crash the debounce handler.
    grid._emit_visible_rows()
    grid._emit_visible_rows_impl()


def test_real_errors_are_no_longer_swallowed(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid = _grid(monkeypatch, ValueError("rowCount exploded"))

    with pytest.raises(ValueError):
        grid._emit_visible_rows_impl()

    # The timer-facing wrapper only tolerates Qt teardown, so real errors surface.
    with pytest.raises(ValueError):
        grid._emit_visible_rows()
