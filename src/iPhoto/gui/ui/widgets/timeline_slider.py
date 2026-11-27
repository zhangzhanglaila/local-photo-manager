"""Timeline range slider widget for date range filtering."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import (
    QDate,
    QPoint,
    QRect,
    QRegularExpression,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _SpinEdit(QLineEdit):
    """A compact spin-edit with up/down arrows that support long-press repeat."""

    valueChanged = Signal()

    _REPEAT_INITIAL_MS = 400   # delay before repeat starts
    _REPEAT_INTERVAL_MS = 60   # interval between repeats

    def __init__(
        self,
        value: int,
        min_val: int,
        max_val: int,
        width: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(str(value), parent)
        self._min = min_val
        self._max = max_val
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLineEdit { font-size: 11px; border: 1px solid #D0D5DD; "
            "border-radius: 3px; padding: 1px 2px; background: #fff; }"
            "QLineEdit:focus { border-color: #356CB4; }"
        )
        rx = QRegularExpression(r"\d+")
        self.setValidator(QRegularExpressionValidator(rx, self))
        self.editingFinished.connect(self._on_edit_finished)

        # Long-press repeat timer
        self._repeat_timer = QTimer(self)
        self._repeat_timer.setSingleShot(False)
        self._repeat_step = 0  # +1 or -1

        # Up button
        self._btn_up = QToolButton()
        self._btn_up.setArrowType(Qt.ArrowType.UpArrow)
        self._btn_up.setFixedSize(14, 12)
        self._btn_up.setAutoRaise(True)
        self._btn_up.setStyleSheet("QToolButton { border: none; }")
        self._btn_up.pressed.connect(self._on_up_pressed)
        self._btn_up.released.connect(self._on_repeat_released)

        # Down button
        self._btn_down = QToolButton()
        self._btn_down.setArrowType(Qt.ArrowType.DownArrow)
        self._btn_down.setFixedSize(14, 12)
        self._btn_down.setAutoRaise(True)
        self._btn_down.setStyleSheet("QToolButton { border: none; }")
        self._btn_down.pressed.connect(self._on_down_pressed)
        self._btn_down.released.connect(self._on_repeat_released)

    # -- long-press repeat ------------------------------------------------
    def _on_up_pressed(self) -> None:
        self._step_up()
        self._repeat_step = +1
        self._repeat_timer.timeout.connect(self._repeat_tick)
        self._repeat_timer.start(self._REPEAT_INITIAL_MS)

    def _on_down_pressed(self) -> None:
        self._step_down()
        self._repeat_step = -1
        self._repeat_timer.timeout.connect(self._repeat_tick)
        self._repeat_timer.start(self._REPEAT_INITIAL_MS)

    def _on_repeat_released(self) -> None:
        self._repeat_timer.stop()
        try:
            self._repeat_timer.timeout.disconnect(self._repeat_tick)
        except (TypeError, RuntimeError):
            pass
        self._repeat_step = 0

    def _repeat_tick(self) -> None:
        self._repeat_timer.setInterval(self._REPEAT_INTERVAL_MS)
        if self._repeat_step > 0:
            self._step_up()
        elif self._repeat_step < 0:
            self._step_down()

    # -- value logic -----------------------------------------------------
    def _step_up(self) -> None:
        val = int(self.text() or str(self._min))
        if val < self._max:
            self.setText(str(val + 1))
            self.valueChanged.emit()

    def _step_down(self) -> None:
        val = int(self.text() or str(self._min))
        if val > self._min:
            self.setText(str(val - 1))
            self.valueChanged.emit()

    def _on_edit_finished(self) -> None:
        try:
            val = int(self.text() or "0")
        except ValueError:
            val = self._min
        val = max(self._min, min(self._max, val))
        self.setText(str(val))
        self.valueChanged.emit()

    def value(self) -> int:
        try:
            return max(self._min, min(self._max, int(self.text() or "0")))
        except ValueError:
            return self._min

    def set_value(self, val: int) -> None:
        self.setText(str(max(self._min, min(self._max, val))))


class _DateEditor(QWidget):
    """Inline date editor: YYYY ↑↓ / MM ↑↓ / DD ↑↓ + calendar popup.

    No dialog window — the calendar appears as a popup anchored below
    the editor and auto-closes on selection or focus loss.
    """

    dateChanged = Signal(datetime)
    _CAL_POPUP = None  # class-level singleton to avoid multiple popups

    def __init__(self, dt: datetime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dt = dt
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        # Year (4-digit)
        y_wrap = QVBoxLayout()
        y_wrap.setContentsMargins(0, 0, 0, 0)
        y_wrap.setSpacing(0)
        self._year_edit = _SpinEdit(self._dt.year, 1970, 2099, 42)
        y_wrap.addWidget(self._year_edit._btn_up, 0, Qt.AlignmentFlag.AlignCenter)
        y_wrap.addWidget(self._year_edit)
        y_wrap.addWidget(self._year_edit._btn_down, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(y_wrap)

        dash1 = QLabel("-")
        dash1.setStyleSheet("font-size: 11px; color: #999; padding: 0 1px;")
        dash1.setFixedWidth(6)
        layout.addWidget(dash1)

        # Month
        m_wrap = QVBoxLayout()
        m_wrap.setContentsMargins(0, 0, 0, 0)
        m_wrap.setSpacing(0)
        self._month_edit = _SpinEdit(self._dt.month, 1, 12, 26)
        m_wrap.addWidget(self._month_edit._btn_up, 0, Qt.AlignmentFlag.AlignCenter)
        m_wrap.addWidget(self._month_edit)
        m_wrap.addWidget(self._month_edit._btn_down, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(m_wrap)

        dash2 = QLabel("-")
        dash2.setStyleSheet("font-size: 11px; color: #999; padding: 0 1px;")
        dash2.setFixedWidth(6)
        layout.addWidget(dash2)

        # Day
        d_wrap = QVBoxLayout()
        d_wrap.setContentsMargins(0, 0, 0, 0)
        d_wrap.setSpacing(0)
        max_day = self._days_in_month(self._dt.year, self._dt.month)
        self._day_edit = _SpinEdit(self._dt.day, 1, max_day, 26)
        d_wrap.addWidget(self._day_edit._btn_up, 0, Qt.AlignmentFlag.AlignCenter)
        d_wrap.addWidget(self._day_edit)
        d_wrap.addWidget(self._day_edit._btn_down, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(d_wrap)

        # Calendar icon button
        self._cal_btn = QToolButton()
        self._cal_btn.setText("📅")
        self._cal_btn.setFixedSize(18, 18)
        self._cal_btn.setAutoRaise(True)
        self._cal_btn.setStyleSheet("QToolButton { border: none; font-size: 11px; }")
        self._cal_btn.clicked.connect(self._toggle_calendar)
        layout.addWidget(self._cal_btn)

        # Connect change signals
        self._year_edit.valueChanged.connect(self._on_field_changed)
        self._month_edit.valueChanged.connect(self._on_field_changed)
        self._day_edit.valueChanged.connect(self._on_field_changed)

        self.setFixedHeight(42)

    def set_date(self, dt: datetime) -> None:
        self._dt = dt
        self._year_edit.set_value(dt.year)
        self._month_edit.set_value(dt.month)
        self._day_edit.set_value(dt.day)
        self._update_day_range()

    def date(self) -> datetime:
        return self._dt

    def _on_field_changed(self) -> None:
        y = self._year_edit.value()
        m = self._month_edit.value()
        max_day = self._days_in_month(y, m)
        d = min(self._day_edit.value(), max_day)
        self._day_edit._max = max_day
        if self._day_edit.value() > max_day:
            self._day_edit.set_value(d)
        new_dt = datetime(y, m, d)
        if new_dt != self._dt:
            self._dt = new_dt
            self.dateChanged.emit(new_dt)

    def _update_day_range(self) -> None:
        max_day = self._days_in_month(self._dt.year, self._dt.month)
        self._day_edit._max = max_day
        if self._day_edit.value() > max_day:
            self._day_edit.set_value(max_day)

    @staticmethod
    def _days_in_month(y: int, m: int) -> int:
        if m == 2:
            return 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28
        if m in (4, 6, 9, 11):
            return 30
        return 31

    # ------------------------------------------------------------------
    # Calendar popup (anchored, modeless)
    # ------------------------------------------------------------------
    def _toggle_calendar(self) -> None:
        if _DateEditor._CAL_POPUP is not None:
            _DateEditor._CAL_POPUP.close()
            _DateEditor._CAL_POPUP = None
            return

        popup = _CalendarPopup(
            self._dt,
            self.window(),
            on_selected=self._on_calendar_selected,
            on_dismissed=self._on_calendar_dismissed,
        )
        # Anchor below the editor
        pos = self.mapToGlobal(QPoint(0, self.height()))
        popup.move(pos)
        popup.show()
        _DateEditor._CAL_POPUP = popup

    def _on_calendar_selected(self, dt: datetime) -> None:
        if dt != self._dt:
            self._dt = dt
            self._year_edit.set_value(dt.year)
            self._month_edit.set_value(dt.month)
            self._day_edit.set_value(dt.day)
            self._update_day_range()
            self.dateChanged.emit(dt)

    def _on_calendar_dismissed(self) -> None:
        _DateEditor._CAL_POPUP = None


class _CalendarPopup(QCalendarWidget):
    """Frameless calendar that closes on selection or focus loss."""

    def __init__(
        self,
        dt: datetime,
        parent: QWidget,
        *,
        on_selected,
        on_dismissed,
    ) -> None:
        super().__init__(parent)
        self._on_selected = on_selected
        self._on_dismissed = on_dismissed
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setSelectedDate(QDate(dt.year, dt.month, dt.day))
        self.setGridVisible(True)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setFixedSize(280, 220)
        self.clicked.connect(self._on_clicked)
        self.activated.connect(self._on_clicked)

    def _on_clicked(self, qd: QDate) -> None:
        new_dt = datetime(qd.year(), qd.month(), qd.day())
        self._on_selected(new_dt)
        self.close()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.close()

    def closeEvent(self, event) -> None:
        self._on_dismissed()
        super().closeEvent(event)


class TimelineSlider(QWidget):
    """Range slider for selecting a date range on the photo trail.

    Shows a visual track with two arrow handles (◀ ▶) representing
    start (left) and end (right).  The highlighted area between them
    is the active date range.  Dates are editable inline via spin
    fields with a calendar popup (not a dialog window).

    Signals
    -------
    rangeChanged(datetime, datetime)
        Emitted when the selected date range changes.
    granularityChanged(str)
        Emitted when the granularity changes ("day", "week", "month").
    """

    rangeChanged = Signal(datetime, datetime)
    granularityChanged = Signal(str)

    HANDLE_SIZE = 12
    TRACK_HEIGHT = 6
    HANDLE_HIT_PADDING = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start_date = datetime(2000, 1, 1)
        self._end_date = datetime.now()
        self._current_start = self._start_date
        self._current_end = self._end_date
        self._granularity = "day"
        self._updating = False
        self._dragging: Optional[str] = None  # "start" | "end" | None

        self.setMinimumHeight(44)
        self.setMouseTracking(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 2, 8, 4)
        root.setSpacing(4)

        # --- Track area (custom painted) ---
        self._track = QWidget()
        self._track.setFixedHeight(self.HANDLE_SIZE + self.TRACK_HEIGHT + 10)
        self._track.setMouseTracking(True)
        self._track.mousePressEvent = self._track_mouse_press
        self._track.mouseMoveEvent = self._track_mouse_move
        self._track.mouseReleaseEvent = self._track_mouse_release
        self._track.paintEvent = self._paint_track
        root.addWidget(self._track)

        # --- Bottom row: date editors + granularity buttons ---
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)

        self._start_editor = _DateEditor(self._current_start)
        self._start_editor.dateChanged.connect(self._on_start_date_changed)
        bottom.addWidget(self._start_editor)

        bottom.addStretch(1)

        self._btn_day = QPushButton("Day")
        self._btn_week = QPushButton("Week")
        self._btn_month = QPushButton("Month")
        for btn in (self._btn_day, self._btn_week, self._btn_month):
            btn.setCheckable(True)
            btn.setFixedWidth(46)
            btn.setFixedHeight(22)
            btn.setStyleSheet(
                "QPushButton { font-size: 11px; border: 1px solid #ccc; "
                "border-radius: 4px; padding: 2px 6px; }"
                "QPushButton:checked { background: #356CB4; color: white; "
                "border-color: #356CB4; }"
            )
            btn.clicked.connect(self._on_granularity_clicked)
        self._btn_day.setChecked(True)
        bottom.addWidget(self._btn_day)
        bottom.addWidget(self._btn_week)
        bottom.addWidget(self._btn_month)

        bottom.addStretch(1)

        self._end_editor = _DateEditor(self._current_end)
        self._end_editor.dateChanged.connect(self._on_end_date_changed)
        bottom.addWidget(self._end_editor)

        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_date_range(self, start: datetime, end: datetime) -> None:
        """Set the available date range."""
        self._updating = True
        self._start_date = start
        self._end_date = end
        self._current_start = start
        self._current_end = end
        self._start_editor.set_date(start)
        self._end_editor.set_date(end)
        self._track.update()
        self._updating = False

    @property
    def current_start(self) -> datetime:
        return self._current_start

    @property
    def current_end(self) -> datetime:
        return self._current_end

    @property
    def granularity(self) -> str:
        return self._granularity

    # ------------------------------------------------------------------
    # Track geometry
    # ------------------------------------------------------------------
    def _track_rect(self) -> QRect:
        w = self._track.width()
        h = self._track.height()
        margin = self.HANDLE_SIZE + 4
        return QRect(
            margin, h // 2 - self.TRACK_HEIGHT // 2,
            max(w - 2 * margin, 0), self.TRACK_HEIGHT,
        )

    def _value_to_x(self, dt: datetime) -> int:
        total = (self._end_date - self._start_date).total_seconds()
        if total <= 0:
            return self._track_rect().left()
        frac = (dt - self._start_date).total_seconds() / total
        tr = self._track_rect()
        return int(tr.left() + frac * tr.width())

    def _x_to_value(self, x: int) -> datetime:
        tr = self._track_rect()
        if tr.width() <= 0:
            return self._start_date
        frac = max(0.0, min(1.0, (x - tr.left()) / tr.width()))
        total = (self._end_date - self._start_date).total_seconds()
        return self._start_date + timedelta(seconds=total * frac)

    def _handle_hit_rect(self, which: str) -> QRect:
        """Hit-test rectangle for a handle (padded for easier grabbing)."""
        dt = self._current_start if which == "start" else self._current_end
        cx = self._value_to_x(dt)
        cy = self._track.height() // 2
        s = self.HANDLE_SIZE + self.HANDLE_HIT_PADDING
        return QRect(cx - s, cy - s, s * 2, s * 2)

    # ------------------------------------------------------------------
    # Track painting — arrow handles
    # ------------------------------------------------------------------
    def _paint_track(self, event) -> None:
        painter = QPainter(self._track)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        tr = self._track_rect()
        cy = self._track.height() // 2
        hs = self.HANDLE_SIZE

        # Background track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#DDE1E6"))
        painter.drawRoundedRect(
            tr, self.TRACK_HEIGHT // 2, self.TRACK_HEIGHT // 2,
        )

        # Active range highlight (between handles)
        sx = self._value_to_x(self._current_start)
        ex = self._value_to_x(self._current_end)
        if ex > sx:
            active = QRect(sx, tr.top(), ex - sx, tr.height())
            painter.setBrush(QColor("#356CB4"))
            painter.drawRoundedRect(
                active, self.TRACK_HEIGHT // 2, self.TRACK_HEIGHT // 2,
            )

        # Arrow handles — filled with a bold outline for clarity.
        handle_pen = QPen(QColor("#1D4ED8"), 2.0)
        handle_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(handle_pen)
        painter.setBrush(QColor("#FFFFFF"))

        # Left handle: ◀ pointing right (toward the range)
        self._draw_arrow(painter, sx, cy, hs, pointing_right=True)
        # Right handle: ▶ pointing left (toward the range)
        self._draw_arrow(painter, ex, cy, hs, pointing_right=False)

        painter.end()

    @staticmethod
    def _draw_arrow(
        painter: QPainter, cx: int, cy: int, size: int, *, pointing_right: bool,
    ) -> None:
        """Draw a filled triangular arrow handle at (*cx*, *cy*)."""
        half = size
        if pointing_right:
            # ▶  points to the right
            points = [
                QPoint(cx - half * 3 // 5, cy - half),
                QPoint(cx + half * 3 // 5, cy),
                QPoint(cx - half * 3 // 5, cy + half),
            ]
        else:
            # ◀  points to the left
            points = [
                QPoint(cx + half * 3 // 5, cy - half),
                QPoint(cx - half * 3 // 5, cy),
                QPoint(cx + half * 3 // 5, cy + half),
            ]
        path = QPainterPath()
        path.moveTo(points[0])
        path.lineTo(points[1])
        path.lineTo(points[2])
        path.closeSubpath()
        # Draw filled shape with border
        painter.drawPath(path)

    # ------------------------------------------------------------------
    # Mouse interaction for handle dragging
    # ------------------------------------------------------------------
    def _hit_handle(self, pos: QPoint) -> Optional[str]:
        for which in ("start", "end"):
            if self._handle_hit_rect(which).contains(pos):
                return which
        return None

    def _track_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit_handle(event.pos())
        if hit:
            self._dragging = hit
            self._track.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            click_dt = self._x_to_value(event.pos().x())
            dist_start = abs((click_dt - self._current_start).total_seconds())
            dist_end = abs((click_dt - self._current_end).total_seconds())
            self._dragging = "start" if dist_start <= dist_end else "end"
            self._apply_drag(event.pos().x())

    def _track_mouse_move(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._apply_drag(event.pos().x())
        else:
            hit = self._hit_handle(event.pos())
            self._track.setCursor(
                Qt.CursorShape.SizeHorCursor if hit else Qt.CursorShape.ArrowCursor,
            )

    def _track_mouse_release(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._apply_drag(event.pos().x())
            self._dragging = None
            self._track.setCursor(Qt.CursorShape.ArrowCursor)
            self.rangeChanged.emit(self._current_start, self._current_end)

    def _apply_drag(self, x: int) -> None:
        new_dt = self._x_to_value(x)
        if self._dragging == "start":
            if new_dt >= self._current_end:
                new_dt = self._current_end - timedelta(seconds=1)
            if new_dt < self._start_date:
                new_dt = self._start_date
            if new_dt != self._current_start:
                self._current_start = new_dt
                self._start_editor.set_date(new_dt)
                self._track.update()
        else:
            if new_dt <= self._current_start:
                new_dt = self._current_start + timedelta(seconds=1)
            if new_dt > self._end_date:
                new_dt = self._end_date
            if new_dt != self._current_end:
                self._current_end = new_dt
                self._end_editor.set_date(new_dt)
                self._track.update()

    # ------------------------------------------------------------------
    # Date editor callbacks
    # ------------------------------------------------------------------
    def _on_start_date_changed(self, dt: datetime) -> None:
        if dt >= self._current_end:
            dt = self._current_end - timedelta(days=1)
        if dt < self._start_date:
            dt = self._start_date
        self._current_start = dt
        self._start_editor.set_date(dt)
        self._track.update()
        self.rangeChanged.emit(self._current_start, self._current_end)

    def _on_end_date_changed(self, dt: datetime) -> None:
        if dt <= self._current_start:
            dt = self._current_start + timedelta(days=1)
        if dt > self._end_date:
            dt = self._end_date
        self._current_end = dt
        self._end_editor.set_date(dt)
        self._track.update()
        self.rangeChanged.emit(self._current_start, self._current_end)

    # ------------------------------------------------------------------
    # Granularity
    # ------------------------------------------------------------------
    def _on_granularity_clicked(self) -> None:
        sender = self.sender()
        if sender is self._btn_day:
            g = "day"
        elif sender is self._btn_week:
            g = "week"
        else:
            g = "month"

        if g == self._granularity:
            return

        self._granularity = g
        self._btn_day.setChecked(g == "day")
        self._btn_week.setChecked(g == "week")
        self._btn_month.setChecked(g == "month")
        self.granularityChanged.emit(g)
