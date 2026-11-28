"""Caption-button hover hints follow the UI language and the window state.

The title bar used to hard-code English tooltips and the caption tooltips were
retranslated only when the window was rebuilt, so a Chinese UI showed
"Minimize" / "Maximize" next to the translated menus.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip(
    "PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError
)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.custom_title_bar import CustomTitleBar
from iPhoto.i18n import set_language


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    set_language("zh")


def test_caption_tooltips_follow_the_selected_language(qapp: QApplication) -> None:
    set_language("zh")
    title_bar = CustomTitleBar(window_title="iPhoto")
    assert title_bar.minimize_button.toolTip() == "最小化"
    assert title_bar.maximize_button.toolTip() == "最大化"
    assert title_bar.close_button.toolTip() == "关闭"

    set_language("en")
    title_bar.retranslate(False)
    assert title_bar.minimize_button.toolTip() == "Minimize"
    assert title_bar.maximize_button.toolTip() == "Maximize"
    assert title_bar.close_button.toolTip() == "Close"

    title_bar.close()


def test_maximize_tooltip_and_glyph_follow_the_window_state(qapp: QApplication) -> None:
    set_language("zh")
    title_bar = CustomTitleBar(window_title="iPhoto")

    title_bar.set_maximized(True)
    assert title_bar.maximize_button.toolTip() == "还原"
    assert title_bar.maximize_button._icon_name == "window.restore.svg"

    # A language switch without a state argument keeps the restore hint.
    set_language("en")
    title_bar.retranslate()
    assert title_bar.maximize_button.toolTip() == "Restore"
    assert title_bar.maximize_button._icon_name == "window.restore.svg"

    title_bar.set_maximized(False)
    assert title_bar.maximize_button.toolTip() == "Maximize"
    assert title_bar.maximize_button._icon_name == "window.maximize.svg"

    title_bar.close()
