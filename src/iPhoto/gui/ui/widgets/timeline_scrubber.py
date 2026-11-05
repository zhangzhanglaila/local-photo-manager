"""Right-edge year rail for the photo gallery.

The rail mirrors what macOS Photos and Google Photos do: a compact column of
years that can be clicked to jump through the library. It deliberately owns no
data of its own — the caller hands it ``(year, first_row)`` pairs, which come from
a single aggregate query, so painting and hit-testing stay O(number of years).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

# Rail geometry. Keep the rail narrow so it never competes with the thumbnails.
RAIL_WIDTH = 58
LABEL_RIGHT_PADDING = 6
TICK_LENGTH = 7
VERTICAL_INSET = 18

# Animation-free styling: a subtle grey label, accent when active.
LABEL_COLOR = QColor(60, 60, 67, 140)
LABEL_ACTIVE_COLOR = QColor("#007AFF")
TICK_COLOR = QColor(60, 60, 67, 70)
TICK_ACTIVE_COLOR = QColor("#007AFF")


class TimelineScrubber(QWidget):
    """Vertical year rail that jumps the grid to a year."""

    yearActivated = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._years: list[tuple[int, int]] = []
        self._active_year: int | None = None
        self._hover_year: int | None = None
        self.setFixedWidth(RAIL_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.hide()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def set_years(self, years: list[tuple[int, int]]) -> None:
        """Set ``(year, first_row)`` pairs ordered newest first."""

        self._years = [(int(year), int(row)) for year, row in years]
        if self._active_year is not None and not any(
            year == self._active_year for year, _row in self._years
        ):
            self._active_year = None
        # Two years is not worth a rail; the grid already shows dates.
        self.setVisible(len(self._years) >= 2)
        self.update()

    def years(self) -> list[tuple[int, int]]:
        return list(self._years)

    def set_active_year(self, year: int | None) -> None:
        if self._active_year == year:
            return
        self._active_year = year
        self.update()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _label_rects(self) -> list[tuple[int, int, QRectF]]:
        """Return ``(year, first_row, rect)`` for every year label."""

        count = len(self._years)
        if count == 0:
            return []

        usable_top = float(VERTICAL_INSET)
        usable_height = max(1.0, float(self.height()) - 2 * VERTICAL_INSET)
        step = usable_height / max(1, count - 1) if count > 1 else 0.0
        rects: list[tuple[int, int, QRectF]] = []
        for index, (year, row) in enumerate(self._years):
            center = usable_top + step * index
            rect = QRectF(
                0.0,
                center - 9.0,
                float(self.width() - LABEL_RIGHT_PADDING - TICK_LENGTH - 4),
                18.0,
            )
            rects.append((year, row, rect))
        return rects

    def year_at(self, y: int) -> tuple[int, int] | None:
        """Return the ``(year, row)`` nearest to a widget-local *y* coordinate."""

        rects = self._label_rects()
        if not rects:
            return None
        best = min(rects, key=lambda item: abs(item[2].center().y() - y))
        return best[0], best[1]

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        rects = self._label_rects()
        if not rects:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # A translucent plate keeps the years readable over bright photos without
        # hiding the thumbnails completely.
        panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        panel_color = QColor(255, 255, 255, 150)
        blink = self.palette().color(self.backgroundRole())
        if blink.lightness() < 128:
            panel_color = QColor(28, 28, 30, 170)
        painter.setPen(QPen(QColor(0, 0, 0, 18), 1.0))
        painter.setBrush(panel_color)
        painter.drawRoundedRect(panel, 12.0, 12.0)

        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)

        for year, _row, rect in rects:
            active = year == self._active_year
            hovered = year == self._hover_year
            color = LABEL_ACTIVE_COLOR if active else LABEL_COLOR
            if hovered and not active:
                color = QColor(60, 60, 67, 210)
            painter.setPen(color)
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                str(year),
            )

            tick_color = TICK_ACTIVE_COLOR if active else TICK_COLOR
            painter.setPen(QPen(tick_color, 1.0))
            center_y = rect.center().y()
            painter.drawLine(
                QRectF(rect.right() + 2, center_y, TICK_LENGTH, 0).topLeft(),
                QRectF(rect.right() + 2 + TICK_LENGTH, center_y, 0, 0).topLeft(),
            )

        painter.end()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self.year_at(int(event.position().y()))
            if hit is not None:
                self.yearActivated.emit(hit[1])
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        hit = self.year_at(int(event.position().y()))
        hover = hit[0] if hit is not None else None
        if hover != self._hover_year:
            self._hover_year = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._hover_year is not None:
            self._hover_year = None
            self.update()
        super().leaveEvent(event)


__all__ = ["TimelineScrubber"]
