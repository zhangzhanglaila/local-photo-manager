"""The grid must keep showing type badges (live / video / favourite).

Rounded thumbnails are clipped with a QPainterPath. Resetting that clip with an
empty path clips everything away, so every badge drawn afterwards silently
disappeared and photos became indistinguishable from live photos and videos.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate


class _FakeIndex:
    """Minimal stand-in for a QModelIndex consumed by ``paint``."""

    def __init__(self, data: dict[int, object]) -> None:
        self._data = data

    def data(self, role: int):
        return self._data.get(int(role))


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _paint(qapp: QApplication, data: dict[int, object]) -> QImage:
    size = 120
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))

    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("#3366CC"))

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, size, size)
    option.state = QStyle.State_Enabled

    delegate = AssetGridDelegate()
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, _FakeIndex({int(Qt.DecorationRole): pixmap, **data}))
    finally:
        painter.end()
    return image


def _differing_pixels(left: QImage, right: QImage) -> int:
    count = 0
    for y in range(left.height()):
        for x in range(left.width()):
            if left.pixelColor(x, y) != right.pixelColor(x, y):
                count += 1
    return count


def test_badges_are_painted_on_top_of_the_rounded_thumbnail(qapp: QApplication) -> None:
    plain = _paint(qapp, {})

    live = _paint(qapp, {int(Roles.IS_LIVE): True})
    video = _paint(qapp, {int(Roles.IS_VIDEO): True, int(Roles.SIZE): {"duration": 12}})

    assert _differing_pixels(plain, live) > 50, "the live-photo badge is missing"
    assert _differing_pixels(plain, video) > 50, "the video duration badge is missing"


def test_rounded_corners_still_clip_the_thumbnail(qapp: QApplication) -> None:
    """The badge fix must not undo the rounded corners themselves."""

    image = _paint(qapp, {})

    assert image.pixelColor(0, 0) == QColor("#FFFFFF"), "the rounded corner stays unpainted"
    assert image.pixelColor(image.width() // 2, image.height() // 2) != QColor("#FFFFFF")
