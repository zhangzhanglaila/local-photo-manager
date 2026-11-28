"""The floating tooltip frame must hug its text.

The popup used to reserve one extra line height below every hint, and its paint
rectangle was one pixel narrower than the measured text: short Chinese hints
such as ``最小化`` wrapped onto a second line inside an oversized box.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.custom_tooltip import FloatingToolTip


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_short_hint_stays_on_one_line_and_fits_the_text(qapp: QApplication) -> None:
    tip = FloatingToolTip()
    text = "最小化"
    tip.setText(text)
    hint = tip.sizeHint()

    inset = tip._padding + tip._border_width
    metrics = QFontMetrics(tip._font)
    inner_width = hint.width() - 2 * inset
    inner_height = hint.height() - 2 * inset

    # One pixel of slack: rounding a glyph-exact rectangle down made the
    # painter wrap the final character.
    assert inner_width >= metrics.horizontalAdvance(text) + 1
    # A single line, not a line plus an empty safety line.
    assert inner_height < 2 * metrics.lineSpacing()


def test_long_hint_wraps_and_stays_within_the_width_budget(qapp: QApplication) -> None:
    tip = FloatingToolTip()
    tip.setText("word " * 200)
    hint = tip.sizeHint()

    inset = tip._padding + tip._border_width
    metrics = QFontMetrics(tip._font)

    assert hint.width() <= tip._MAX_WIDTH
    assert hint.height() - 2 * inset >= 2 * metrics.lineSpacing()
