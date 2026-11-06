"""The main window must actually provide every widget the coordinators wire up.

The coordinator used ``hasattr(ui, "widget")`` before every connection. Those
guards silently skipped wiring when a widget or signal was renamed — the feature
simply disappeared with no exception and no log. This test turns that into a
failure at test time instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtWidgets import QApplication, QMainWindow

from iPhoto.gui.ui.ui_contract import WIRED_UI_ATTRS
from iPhoto.gui.ui.ui_main_window import Ui_MainWindow
from iPhoto.library.runtime_controller import LibraryRuntimeController


@pytest.fixture()
def window(qapp: QApplication, tmp_path: Path):
    library_root = tmp_path / "Library"
    library_root.mkdir()
    library = LibraryRuntimeController()
    library.bind_path(library_root)

    main_window = QMainWindow()
    ui = Ui_MainWindow()
    ui.setupUi(main_window, library)
    try:
        yield main_window, ui
    finally:
        main_window.close()
        library.shutdown()


def test_every_wired_attribute_exists(window) -> None:
    _main_window, ui = window

    missing = [name for name in WIRED_UI_ATTRS if not hasattr(ui, name)]

    assert missing == [], f"coordinators depend on these UI attributes: {missing}"


def test_contracted_attributes_are_not_none(window) -> None:
    _main_window, ui = window

    null_attrs = [name for name in WIRED_UI_ATTRS if getattr(ui, name, None) is None]

    assert null_attrs == [], f"these widgets exist but are null: {null_attrs}"
