"""Right-edge year rail for the photo gallery.

The rail mirrors the timeline scrubber used by photo managers: a hairline track
with a round knob at the top, one tick and label per year, and — importantly —
vertical space allocated in proportion to how many photos each year holds.

It owns no data of its own: the caller hands it ``(year, first_row, count)``
triples that come from one aggregate query, so painting and hit-testing stay
O(number of years).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

# Rail geometry. ``LABEL_RESERVE`` is the room labels need; the track sits at the
# right edge of that area so labels never straddle the photos.
RAIL_WIDTH = 52
TRACK_RIGHT_INSET = 7
LABEL_RIGHT_INSET = 16
TICK_LENGTH = 6
TOP_INSET = 26
BOTTOM_INSET = 12
KNOB_RADIUS = 4.0
KNOB_CENTER_Y = 11.0
MIN_LABEL_GAP = 17.0

# Colours: deliberately quiet so the rail reads as chrome, not content.
TRACK_COLOR = QColor(60, 60, 67, 55)
TICK_COLOR = QColor(60, 60, 67, 70)
LABEL_COLOR = QColor(60, 60, 67, 150)
LABEL_ACTIVE_COLOR = QColor("#007AFF")


class TimelineScrubber(QWidget):
    """Vertical year rail that jumps the grid to a year."""

    #: ``(row, y)`` while the pointer moves over the rail without pressing.
    hoverRowChanged = Signal(int, int)
    #: ``(row, y)`` while the rail is being dragged (continuous scrubbing).
    scrubMoved = Signal(int, int)
    scrubFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spans: list[tuple[int, int, int]] = []
        self._layout_y: list[float] = []
        self._active_year: int | None = None
        self._hover_year: int | None = None
        self._scrubbing = False
        self.setFixedWidth(RAIL_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def set_spans(self, spans: list[tuple[int, int, int]]) -> None:
        """Set ``(year, first_row, count)`` triples ordered newest first."""

        self._spans = [
            (int(year), int(row), max(0, int(count))) for year, row, count in spans
        ]
        if self._active_year is not None and not any(
            year == self._active_year for year, _row, _count in self._spans
        ):
            self._active_year = None
        self._relayout()
        # A single year is not worth a rail; the grid already shows dates.
        self.setVisible(len(self._spans) >= 2)
        self.update()

    def set_years(self, years: list[tuple[int, int]]) -> None:
        """Compatibility helper for callers that only know year starts."""

        self.set_spans([(year, row, 1) for year, row in years])

    def spans(self) -> list[tuple[int, int, int]]:
        return list(self._spans)

    def years(self) -> list[tuple[int, int]]:
        return [(year, row) for year, row, _count in self._spans]

    def set_active_year(self, year: int | None) -> None:
        if self._active_year == year:
            return
        self._active_year = year
        self.update()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _relayout(self) -> None:
        """Place each year proportionally to its photo count.

        Pure proportional placement would stack sparse years on top of each other
        (a three-photo year next to a three-thousand-photo year), so a minimum gap
        is enforced afterwards while keeping chronological order.
        """

        self._layout_y = []
        if not self._spans:
            return

        total = sum(count for _year, _row, count in self._spans) or 1
        top = float(TOP_INSET)
        usable = max(1.0, float(self.height()) - TOP_INSET - BOTTOM_INSET)

        positions: list[float] = []
        consumed = 0
        for _year, _row, count in self._spans:
            center = (consumed + count / 2.0) / total
            positions.append(top + center * usable)
            consumed += count

        # Push down to satisfy the minimum gap, then pull back inside the track.
        for index in range(1, len(positions)):
            positions[index] = max(positions[index], positions[index - 1] + MIN_LABEL_GAP)
        overflow = positions[-1] - (top + usable)
        if overflow > 0:
            positions = [value - overflow for value in positions]
        for index in range(len(positions) - 2, -1, -1):
            positions[index] = min(positions[index], positions[index + 1] - MIN_LABEL_GAP)
        positions[0] = max(top, positions[0])

        self._layout_y = positions

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def year_at(self, y: int) -> tuple[int, int] | None:
        """Return the ``(year, first_row)`` nearest to a widget-local *y*."""

        if not self._layout_y:
            return None
        index = min(
            range(len(self._layout_y)),
            key=lambda i: abs(self._layout_y[i] - float(y)),
        )
        year, row, _count = self._spans[index]
        return year, row

    def row_at(self, y: int) -> int | None:
        """Map a track position to a row index, interpolating between years.

        Used for both dragging (continuous scrubbing) and hovering (date
        preview). Interpolation is between adjacent year anchors and is clamped to
        the row range, so it stays monotonic even where the minimum-gap layout
        pushed labels apart.
        """

        if not self._layout_y or not self._spans:
            return None

        positions = self._layout_y
        if y <= positions[0]:
            return max(0, self._spans[0][1])
        if y >= positions[-1]:
            last_year, last_row, last_count = self._spans[-1]
            return max(0, last_row + max(0, last_count) - 1)

        for index in range(len(positions) - 1):
            if positions[index] <= y <= positions[index + 1]:
                span = positions[index + 1] - positions[index]
                ratio = 0.0 if span <= 0 else (y - positions[index]) / span
                start_row = self._spans[index][1]
                end_row = self._spans[index + 1][1]
                return int(round(start_row + (end_row - start_row) * ratio))
        return max(0, self._spans[-1][1])

    def total_rows(self) -> int:
        if not self._spans:
            return 0
        _year, last_row, last_count = self._spans[-1]
        return max(0, last_row + max(0, last_count))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        if not self._spans or not self._layout_y:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        track_x = float(self.width() - TRACK_RIGHT_INSET)
        top = float(TOP_INSET) - 10.0
        bottom = float(self.height() - BOTTOM_INSET) + 4.0

        # Hairline track, plus the round knob that caps it (as in the reference).
        painter.setPen(QPen(TRACK_COLOR, 1.0))
        knob_y = KNOB_CENTER_Y
        painter.drawLine(QRectF(track_x, knob_y + KNOB_RADIUS, 0, 0).topLeft(), QRectF(track_x, bottom, 0, 0).topLeft())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(TRACK_COLOR, 1.2))
        painter.drawEllipse(
            QRectF(track_x - KNOB_RADIUS, knob_y - KNOB_RADIUS, KNOB_RADIUS * 2, KNOB_RADIUS * 2)
        )

        font = QFont(self.font())
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)

        for index, (year, _row, _count) in enumerate(self._spans):
            y = self._layout_y[index]
            active = year == self._active_year
            hovered = year == self._hover_year

            painter.setPen(QPen(LABEL_ACTIVE_COLOR if active else TICK_COLOR, 1.0))
            painter.drawLine(
                QRectF(track_x - TICK_LENGTH, y, 0, 0).topLeft(),
                QRectF(track_x, y, 0, 0).topLeft(),
            )

            color = LABEL_ACTIVE_COLOR if active else LABEL_COLOR
            if hovered and not active:
                color = QColor(60, 60, 67, 220)
            painter.setPen(color)
            label_rect = QRectF(0.0, y - 8.0, float(self.width() - LABEL_RIGHT_INSET), 16.0)
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                str(year),
            )

        painter.end()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            y = int(event.position().y())
            self._scrubbing = True
            row = self.row_at(y)
            if row is not None:
                self.scrubMoved.emit(row, y)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        y = int(event.position().y())
        hit = self.year_at(y)
        hover = hit[0] if hit is not None else None
        if hover != self._hover_year:
            self._hover_year = hover
            self.update()

        row = self.row_at(y)
        if row is not None:
            if self._scrubbing:
                self.scrubMoved.emit(row, y)
            else:
                self.hoverRowChanged.emit(row, y)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._scrubbing:
            self._scrubbing = False
            self.scrubFinished.emit()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._hover_year is not None:
            self._hover_year = None
            self.update()
        if not self._scrubbing:
            self.hoverRowChanged.emit(-1, -1)
        super().leaveEvent(event)


__all__ = ["TimelineScrubber"]
