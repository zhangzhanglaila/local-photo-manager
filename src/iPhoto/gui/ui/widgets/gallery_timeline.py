"""Timeline rail and date badge for the gallery grid.

The rail, the scrub preview and the auto-hiding date pill form one subsystem:
the grid itself only forwards pointer/scroll events. Keeping them here leaves
``GalleryGridView`` responsible for the model, empty state and grid layout.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QAbstractItemView

from ....i18n import tr
from ....utils.logging import diag
from ..models.roles import Roles
from .date_pill import DatePill
from .timeline_scrubber import TimelineScrubber


class GalleryTimelineMixin:
    """Scrub a year rail and show the matching date pill over the grid."""

    def _build_timeline_overlays(self) -> None:
        viewport = self.viewport()

        # Parented to the view (not the viewport): the rail lives in the margin
        # reserved by ``setViewportMargins`` so it cannot cover a thumbnail.
        self._timeline_scrubber = TimelineScrubber(self)

        self._date_badge = DatePill(self)

        # Auto-hide the pill shortly after scrolling stops; hovering keeps it up.
        self._date_badge_timer = QTimer(self)
        self._date_badge_timer.setSingleShot(True)
        self._date_badge_timer.setInterval(1100)
        self._date_badge_timer.timeout.connect(self._hide_date_badge)


        # ``visibleRowsChanged`` is already debounced (50 ms) and only fires when
        # the range actually moves, so the badge costs one cached role read per
        # scroll step and never touches the database.
        self.visibleRowsChanged.connect(self._on_visible_rows_for_timeline)
        self._timeline_scrubber.hoverRowChanged.connect(self._on_timeline_hover)
        self._timeline_scrubber.scrubMoved.connect(self._on_timeline_scrub)
        self._timeline_scrubber.scrubFinished.connect(self._on_timeline_scrub_finished)


        # Scrubbing a long rail can emit dozens of positions per second; coalesce
        # the scroll requests instead of fighting the view's layout pass.
        self._scrub_pending_row: int | None = None
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(self.SCRUB_SCROLL_INTERVAL_MS)
        self._scrub_timer.timeout.connect(self._flush_scrub_scroll)

        self._last_hover_row: int | None = None
        self._last_scrub_at: float = 0.0
        self._hover_anchor_y: int | None = None

        # Main-thread stall probe (diag only): a 100 ms heartbeat whose *actual* interval
        # reveals how often the UI thread was blocked, and for how long after startup.
        import time as _time

        self._stall_last = _time.monotonic()
        self._stall_origin = self._stall_last
        self._stall_timer = QTimer(self)
        self._stall_timer.setInterval(100)
        self._stall_timer.timeout.connect(self._probe_ui_stall)
        from ....utils.logging import diag_enabled

        if diag_enabled():
            self._stall_timer.start()

        QTimer.singleShot(0, self._refresh_timeline)

    def _scrubber_y_to_view(self, y: int) -> int:
        """Convert a rail-local y (as reported by its signals) to view coordinates.

        The signals carry coordinates inside the rail widget, while the pill is
        positioned in the grid's coordinate system. Skipping this conversion put the
        pill one rail-offset too high whenever the pointer was on the rail.
        """

        scrubber = getattr(self, "_timeline_scrubber", None)
        if scrubber is None:
            return y
        return int(scrubber.geometry().top() + y)

    def _on_timeline_hover(self, row: int, y: int) -> None:
        """Preview the date under the pointer without moving the grid."""

        if row < 0:
            self._last_hover_row = None
            if not self._scrub_timer.isActive():
                self._hide_date_badge()
            return
        anchor = self._scrubber_y_to_view(y)
        if row == self._last_hover_row:
            self._reposition_date_badge(anchor)
            return
        self._last_hover_row = row
        text = self._date_text_for_row(row)
        if text:
            self._show_date_badge(text, anchor)

    def _probe_ui_stall(self) -> None:
        """Log when the main thread was blocked longer than a frame budget."""

        import time

        now = time.monotonic()
        gap = (now - self._stall_last) * 1000
        self._stall_last = now
        if gap > 250:
            diag(
                "[DIAG] ui stall: %.0f ms at %.0fs after grid start",
                gap,
                now - self._stall_origin,
            )

    def _on_timeline_scrub(self, row: int, y: int) -> None:
        """Continuous scrubbing: the ball follows the pointer immediately.

        The grid itself loads a new window per jump, which is far slower than a
        mouse move. Moving the dot from the drag position (instead of waiting for
        the loaded rows) keeps the ball glued to the cursor; the grid follows at a
        lower cadence and is corrected exactly when the drag ends.
        """

        self._scrub_pending_row = row
        self._last_hover_row = row
        now = time.monotonic()
        previous = getattr(self, "_last_scrub_event_at", None)
        if previous is not None:
            # Gap between consecutive scrub events. The pointer moves every few ms, so a
            # large gap means the main thread was busy elsewhere (not the loader).
            diag("[DIAG] scrub event gap: %.0f ms", (now - previous) * 1000)
        self._last_scrub_event_at = now
        self._last_scrub_at = now
        setter = getattr(self._timeline_scrubber, "set_current_row", None)
        if callable(setter):
            setter(row)
        text = self._date_text_for_row(row, allow_blocking=False)
        if text:
            self._show_date_badge(text, self._scrubber_y_to_view(y))
        self._set_load_apply_throttle(0.25)
        self._set_scan_refresh_suppressed(True)
        self._set_merge_on_apply(True)
        if not self._scrub_timer.isActive():
            self._scrub_timer.start()

    def _is_scrubbing(self) -> bool:
        """True while a drag is active, including a short settle window.

        Without the settle window the first ``visibleRowsChanged`` after the drag
        would snap the dot back to the still-loading position.
        """

        if self._timeline_scrubber.is_scrubbing():
            return True
        return (time.monotonic() - self._last_scrub_at) < self.SCRUB_SETTLE_S

    def _flush_scrub_scroll(self) -> None:
        row = self._scrub_pending_row
        self._scrub_pending_row = None
        if row is None:
            return
        model = self.model()
        if model is None or row < 0 or row >= model.rowCount():
            return
        index = model.index(row, 0)
        if index.isValid():
            self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtTop)

    def _on_timeline_scrub_finished(self) -> None:
        # Release: land exactly on the dragged position instead of the last
        # throttled step.
        self._last_hover_row = None
        self._scrub_timer.stop()
        self._set_load_apply_throttle(0.0)
        self._set_merge_on_apply(False)
        self._set_scan_refresh_suppressed(False)
        self._flush_deferred_load()
        self._flush_scrub_scroll()
        # The month preview becomes a full date once the row is loaded.
        QTimer.singleShot(150, self._refresh_scrub_date)

    def _refresh_scrub_date(self) -> None:
        if not self._date_badge.isVisible():
            return
        anchor = self._dot_anchor_y()
        if anchor is None:
            return
        index = self.indexAt(QPoint(self.viewport().width() // 2, 2))
        if not index.isValid():
            return
        text = self._date_text_for_row(index.row())
        if text:
            self._show_date_badge(text, anchor)
        self._date_badge_timer.start()

    def _date_text_for_row(self, row: int, *, allow_blocking: bool = True) -> str:
        """Resolve a row's date for the pill.

        `allow_blocking=False` is used while dragging: it never queries the
        database, falling back to the month bucket so the ball keeps following the
        pointer. The exact date is filled in once the drag ends.
        """

        model = self.model()
        if model is None:
            return ""
        store = getattr(model, "store", None)
        dto = None
        if store is not None:
            dto = store.peek_asset(row)
            if dto is None and allow_blocking:
                try:
                    dto = store.asset_at(row)
                except Exception:
                    dto = None
        if dto is not None:
            return self._format_timeline_date(getattr(dto, "created_at", None))

        if not allow_blocking:
            month_lookup = getattr(store, "month_for_row", None) if store is not None else None
            if callable(month_lookup):
                try:
                    year_month = month_lookup(row)
                except Exception:
                    year_month = None
                if year_month is not None:
                    year, month = year_month
                    return tr("timeline.month_format", year=year, month=month)
            # No bucket information: the model's own role is still cheap (it reads
            # the cache and queues a fetch) so it is safe on the drag path.

        index = model.index(row, 0)
        if index.isValid():
            return self._format_timeline_date(index.data(Roles.DT))
        return ""

    def _layout_timeline_overlays(self) -> None:
        viewport = self.viewport()
        width = viewport.width()
        height = viewport.height()
        if width <= 0 or height <= 0:
            return

        scrubber = getattr(self, "_timeline_scrubber", None)
        if scrubber is not None:
            rail_width = scrubber.width()
            scrubber.setGeometry(
                max(0, self.width() - rail_width - 4),
                8,
                rail_width,
                max(0, self.height() - 16),
            )
            scrubber.raise_()

        badge = getattr(self, "_date_badge", None)
        if badge is not None and badge.isVisible():
            self._position_date_badge(getattr(self, "_hover_anchor_y", None))

    def _show_date_badge(self, text: str, anchor_y: int | None = None) -> None:
        """Show the date pill; ``anchor_y`` follows the pointer on the rail."""

        badge = getattr(self, "_date_badge", None)
        if badge is None or not text:
            return
        badge.setText(text)
        self._hover_anchor_y = anchor_y
        self._position_date_badge(anchor_y)
        badge.show()
        badge.raise_()

    def _hide_date_badge(self) -> None:
        """Hide the date pill (pointer left the rail)."""

        self._hover_anchor_y = None
        badge = getattr(self, "_date_badge", None)
        if badge is not None:
            badge.hide()

    def _reposition_date_badge(self, anchor_y: int | None) -> None:
        self._hover_anchor_y = anchor_y
        self._position_date_badge(anchor_y)

    def _dot_anchor_y(self) -> int | None:
        """Return the rail dot's y in this view's coordinates."""

        scrubber = getattr(self, "_timeline_scrubber", None)
        if scrubber is None:
            return None
        dot_getter = getattr(scrubber, "dot_y", None)
        dot_y = dot_getter() if callable(dot_getter) else None
        if dot_y is None:
            return None
        return int(scrubber.geometry().top() + dot_y)

    def _position_date_badge(self, anchor_y: int | None = None) -> None:
        """Place the pill beside the track, level with the dot.

        Scrolling and dragging use the same anchor: the position dot's height. The
        pill is therefore always next to the ball instead of jumping to the top of
        the grid.
        """

        badge = self._date_badge
        hint = badge.sizeHint()
        width = max(24, hint.width())
        height = max(24, hint.height())

        anchor = anchor_y if anchor_y is not None else self._dot_anchor_y()
        scrubber = getattr(self, "_timeline_scrubber", None)
        if anchor is None or scrubber is None:
            badge.setGeometry(
                max(0, (self.width() - width) // 2),
                10,
                width,
                height,
            )
            badge.raise_()
            return

        # The pill's right edge stops just short of the track, so it overlaps the
        # year labels (which stay faintly visible through the translucent fill).
        rail_left = scrubber.geometry().left()
        track_x = rail_left + scrubber.width() - 7
        x = max(0, track_x - 14 - width)
        y = max(2, min(self.height() - height - 2, anchor - height // 2))
        badge.setGeometry(x, y, width, height)
        badge.raise_()

    def _refresh_timeline(self) -> None:
        """Rebuild the year rail from the model's cached year/row mapping."""

        scrubber = getattr(self, "_timeline_scrubber", None)
        if scrubber is None:
            return
        model = self.model()
        spans_provider = getattr(model, "year_spans", None)
        years_provider = getattr(model, "year_start_rows", None)
        if callable(spans_provider):
            try:
                scrubber.set_spans(list(spans_provider()))
            except Exception:
                scrubber.set_years([])
        elif callable(years_provider):
            try:
                scrubber.set_years(list(years_provider()))
            except Exception:
                scrubber.set_years([])
        else:
            scrubber.set_years([])
        self._apply_timeline_margins()
        self._layout_timeline_overlays()

    def _apply_timeline_margins(self) -> None:
        """Reserve room for the rail so it never touches the rightmost column."""

        scrubber = getattr(self, "_timeline_scrubber", None)
        reserve = 0
        if scrubber is not None and scrubber.isVisible():
            # The extra gap keeps a clear channel between photos and the track.
            reserve = scrubber.width() + self.TIMELINE_GAP
        if reserve == getattr(self, "_timeline_reserved_width", 0):
            return
        self._timeline_reserved_width = reserve
        self.setViewportMargins(0, 0, reserve, 0)
        self._update_grid_size()

    def _schedule_timeline_refresh(self) -> None:
        QTimer.singleShot(0, self._refresh_timeline)

    def _on_visible_rows_for_timeline(self, first: int, last: int) -> None:
        """Keep the rail's position dot and active year in sync with the grid.

        Scrolling never shows the date pill: the pill belongs to hovering the rail,
        while the dot is what reports "where the photos currently are".
        """

        index = self.indexAt(QPoint(self.viewport().width() // 2, 2))
        if not index.isValid():
            index = self.model().index(max(0, first), 0) if self.model() else None
        if index is None or not index.isValid():
            return

        created_at = index.data(Roles.DT)
        year = None
        if hasattr(created_at, "year"):
            year = int(created_at.year)
        elif isinstance(created_at, str) and len(created_at) >= 4:
            try:
                year = int(created_at[0:4])
            except ValueError:
                year = None

        scrubber = getattr(self, "_timeline_scrubber", None)
        if scrubber is None:
            return
        scrubber.set_active_year(year)
        # Park the position dot at the row shown at the top of the grid, unless a
        # drag is in progress: then the dot belongs to the pointer position and the
        # grid is still loading its windows.
        if not self._is_scrubbing():
            setter = getattr(scrubber, "set_current_row", None)
            if callable(setter):
                setter(index.row())

        # Scrolling shows the date of the top row and hides it shortly after the
        # scroll stops. While the pointer is on the rail, the pointer position wins
        # so the two update paths do not fight each other.
        if self._hover_anchor_y is None:
            text = self._format_timeline_date(created_at)
            if text:
                self._show_date_badge(text)
                self._date_badge_timer.start()

    @staticmethod
    def _format_timeline_date(created_at: object) -> str:
        """Format a row date for the badge; unknown values stay empty."""

        if hasattr(created_at, "year"):
            try:
                return tr(
                    "timeline.day_format",
                    year=created_at.year,
                    month=created_at.month,
                    day=created_at.day,
                )
            except (AttributeError, ValueError, KeyError):
                return ""
        if isinstance(created_at, str) and len(created_at) >= 10:
            try:
                return tr(
                    "timeline.day_format",
                    year=int(created_at[0:4]),
                    month=int(created_at[5:7]),
                    day=int(created_at[8:10]),
                )
            except ValueError:
                return ""
        return ""

