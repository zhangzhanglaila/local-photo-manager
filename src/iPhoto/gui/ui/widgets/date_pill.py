"""Frosted date pill shown beside the timeline rail.

Implemented as a custom widget on purpose: a ``QLabel`` with a stylesheet radius
plus ``QGraphicsDropShadowEffect`` renders a rectangular shadow and loses the
rounded corners, which is exactly what made the pill look wrong. Painting the
rounded plate and a soft shadow by hand keeps the shape and the blur identical on
every platform, and costs a handful of draw calls on a widget that repaints only
when it moves.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

PADDING_X = 12
PADDING_Y = 5
CORNER_RADIUS = 15.0
TEXT_COLOR = QColor("#1D1D1F")
FILL_COLOR = QColor(255, 255, 255, 216)
BORDER_COLOR = QColor(0, 0, 0, 14)
SHADOW_COLOR = QColor(0, 0, 0, 26)
SHADOW_LAYERS = 6
SHADOW_OFFSET_Y = 2.0


class DatePill(QWidget):
    """Translucent rounded pill with a soft shadow and centered text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFont(self._build_font(self.font()))
        self.hide()

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------
    @staticmethod
    def _build_font(base: QFont) -> QFont:
        font = QFont(base)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def setText(self, text: str) -> None:  # noqa: N802 - Qt-style name
        if text == self._text:
            return
        self._text = text
        self.updateGeometry()
        self.resize(self.sizeHint())
        self.update()

    def text(self) -> str:  # noqa: N802 - Qt-style name
        return self._text

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def sizeHint(self) -> QSize:  # type: ignore[override]
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self._text) + PADDING_X * 2
        height = metrics.height() + PADDING_Y * 2
        return QSize(max(24, width), max(24, height))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        radius = CORNER_RADIUS
        # Soft shadow: stacked translucent rounded rects, cheaper and far more
        # predictable than a graphics effect (which drew a rectangular halo).
        plate = QRectF(self.rect()).adjusted(
            SHADOW_LAYERS, SHADOW_LAYERS, -SHADOW_LAYERS, -SHADOW_LAYERS
        )
        painter.setPen(Qt.PenStyle.NoPen)
        for layer in range(SHADOW_LAYERS, 0, -1):
            alpha = max(2, SHADOW_COLOR.alpha() // (layer + 1))
            color = QColor(SHADOW_COLOR)
            color.setAlpha(alpha)
            painter.setBrush(color)
            offset = float(layer)
            painter.drawRoundedRect(
                plate.adjusted(-offset, -offset + SHADOW_OFFSET_Y, offset, offset + SHADOW_OFFSET_Y),
                radius + offset,
                radius + offset,
            )

        painter.setPen(BORDER_COLOR)
        painter.setBrush(FILL_COLOR)
        painter.drawRoundedRect(plate, radius, radius)

        painter.setPen(TEXT_COLOR)
        painter.setFont(self.font())
        painter.drawText(plate, int(Qt.AlignmentFlag.AlignCenter), self._text)

        painter.end()


__all__ = ["DatePill"]
