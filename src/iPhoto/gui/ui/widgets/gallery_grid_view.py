"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGraphicsDropShadowEffect,
    QLabel,
    QListView,
    QStyleOptionViewItem,
)

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid
from .date_pill import DatePill
from .timeline_scrubber import TimelineScrubber
from ....i18n import tr
from ..models.roles import Roles


class GalleryGridView(AssetGrid):
    """Dense icon-mode grid tuned for album browsing."""

    # Minimum width (and height) for grid items in pixels
    MIN_ITEM_WIDTH = 192

    # Gap between grid items (provides 1px padding on each side)
    ITEM_GAP = 2

    # Safety margin to prevent layout engine from dropping columns due to rounding
    # errors or strict boundary checks. This accounts for frame borders and
    # potential internal margins.
    SAFETY_MARGIN = 10

    # Visible channel between the last thumbnail column and the timeline rail.
    TIMELINE_GAP = 14

    # Grid follow-up cadence while dragging (the ball itself moves every event).
    SCRUB_SCROLL_INTERVAL_MS = 90
    # After the last drag event the loaded rows are still catching up; keep the
    # dot pinned for this long so it does not snap back.
    SCRUB_SETTLE_S = 0.35

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._selection_mode_enabled = False
        self._empty_label = None
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setViewMode(QListView.ViewMode.IconMode)
        # Defer initial size calculation to resizeEvent to avoid rendering the
        # default 192px layout before the viewport dimensions are known.
        self.setSpacing(0)
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWordWrap(False)
        self.setSelectionRectVisible(False)

        # Ensure the viewport paints an opaque background so the gallery is not
        # transparent when the main window uses WA_TranslucentBackground for
        # frameless chrome.
        vp = self.viewport()
        vp.setAutoFillBackground(True)

        self._empty_label = QLabel(
            "No media found. Click Rescan to scan this library.",
            vp,
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #86868b; font-size: 15px;")
        self._empty_label.hide()

        self._loading_label = QLabel(tr("preview.loading"), vp)
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color: #86868b; font-size: 15px;")
        self._loading_label.hide()

        # 扫描状态标签 - 显示扫描中状态
        self._scanning_label = QLabel("正在扫描照片，请稍候...", vp)
        self._scanning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scanning_label.setStyleSheet("color: #86868b; font-size: 15px;")
        self._scanning_label.hide()

        # Suppress "No media found" until the first scan completes so it
        # never flashes during startup.
        self._scan_completed = False
        self._is_scanning = False  # 跟踪是否正在扫描
        # Track whether a query is actively loading so we can show the
        # loading label instead of a blank page.
        self._query_loading = False
        # Context-aware empty message set by coordinator / view-model
        self._empty_mode: str | None = None
        # While the window-level StartupOverlay is on screen it already reports
        # startup/scan progress, so this view must not draw a second placeholder
        # on top of the same empty grid.
        self._startup_overlay_active = False

        # Debounce empty-state updates so rapid model changes during scanning
        # don't cause constant show/hide repaints.
        self._empty_state_timer = QTimer(self)
        self._empty_state_timer.setSingleShot(True)
        self._empty_state_timer.setInterval(100)
        self._empty_state_timer.timeout.connect(self._do_update_empty_state)

        # Safety timer: if no rows appear after a model reset within 2s,
        # assume the query returned empty and stop showing the loading label.
        self._loading_timeout_timer = QTimer(self)
        self._loading_timeout_timer.setSingleShot(True)
        self._loading_timeout_timer.timeout.connect(self._on_loading_timeout)

        self._updating_style = False
        self._apply_scrollbar_style()

        self._build_timeline_overlays()
        self._do_update_empty_state()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        """Paint visible items and one extra row above/below the viewport.

        By pre-rendering items just outside the visible area we prevent the
        blank-flash that occurs when Qt recycles off-screen item widgets and
        the user scrolls them back into view.  The extra row is painted into
        the viewport surface but lies outside the visible region, so it is
        invisible to the user yet ready for immediate display on scroll.
        """
        # Let the base class handle the standard visible items first.
        super().paintEvent(event)

        # Skip extra-row pre-rendering during model resets to avoid
        # dereferencing stale QModelIndex objects at the C++ level.
        if self._model_resetting:
            return

        cell_h = self.gridSize().height()
        cell_w = self.gridSize().width()
        if cell_h <= 0 or cell_w <= 0:
            return

        model = self.model()
        if model is None:
            return
        try:
            row_count = model.rowCount()
        except Exception:
            return
        if row_count == 0:
            return

        delegate = self.itemDelegate()
        if delegate is None:
            return

        vp = self.viewport()
        if vp is None:
            return
        vp_rect = vp.rect()

        # Probe *inside* the viewport to find boundary items, then compute
        # adjacent rows via the column count.  Probing outside the viewport
        # (e.g. ``top()-1``) returns invalid indices in QAbstractItemView,
        # so we determine the row above/below arithmetically instead.
        cols = max(1, vp_rect.width() // cell_w)

        try:
            first_visible = self.indexAt(QPoint(vp_rect.left(), vp_rect.top()))
            bottom_visible = self.indexAt(QPoint(vp_rect.left(), vp_rect.bottom()))
            if not bottom_visible.isValid():
                # Last row may be partial; try the right edge.
                bottom_visible = self.indexAt(QPoint(vp_rect.right(), vp_rect.bottom()))
        except Exception:
            return

        # Re-check after C++ calls — model reset may have started.
        if self._model_resetting:
            return

        # Determine the range of model rows for each extra band.
        extra_indices = []

        try:
            # --- Extra row ABOVE the viewport ---
            if first_visible.isValid():
                vis_row = first_visible.row() // cols
                if vis_row > 0:
                    above_start = (vis_row - 1) * cols
                    first_above = model.index(above_start, 0)
                    above_rect = self.visualRect(first_above)
                    if above_rect.isValid():
                        target_y = above_rect.top()
                        for r in range(above_start, min(above_start + cols, row_count)):
                            idx = model.index(r, 0)
                            r_rect = self.visualRect(idx)
                            if r_rect.isValid() and r_rect.top() == target_y:
                                extra_indices.append((idx, r_rect))

            # --- Extra row BELOW the viewport ---
            if bottom_visible.isValid():
                vis_row = bottom_visible.row() // cols
                below_start = (vis_row + 1) * cols
                if below_start < row_count:
                    first_below = model.index(below_start, 0)
                    below_rect = self.visualRect(first_below)
                    if below_rect.isValid():
                        target_y = below_rect.top()
                        for r in range(below_start, min(below_start + cols, row_count)):
                            idx = model.index(r, 0)
                            r_rect = self.visualRect(idx)
                            if r_rect.isValid() and r_rect.top() == target_y:
                                extra_indices.append((idx, r_rect))
        except Exception:
            return

        if not extra_indices:
            return

        painter = QPainter(vp)
        try:
            for idx, item_rect in extra_indices:
                opt = QStyleOptionViewItem()
                self.initViewItemOption(opt)
                opt.rect = item_rect
                delegate.paint(painter, opt, idx)
        finally:
            painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return

        if self._empty_label is not None:
            self._empty_label.setGeometry(self.viewport().rect())
        if self._loading_label is not None:
            self._loading_label.setGeometry(self.viewport().rect())
        if self._scanning_label is not None:
            self._scanning_label.setGeometry(self.viewport().rect())

        self._layout_timeline_overlays()
        self._update_grid_size()

    def _update_grid_size(self) -> None:
        """Recompute the item size for the current viewport width."""

        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return
        # Determine how many columns can fit with the minimum size constraint.
        # We model the grid cell as (item_width + gap), which provides 1px padding
        # on each side of the item, resulting in a visual 2px gutter between items.
        # We subtract SAFETY_MARGIN to align with the cell_size calculation below,
        # ensuring we don't calculate a column count that immediately fails the
        # minimum size check.
        available_width = viewport_width - self.SAFETY_MARGIN
        num_cols = max(1, int(available_width / (self.MIN_ITEM_WIDTH + self.ITEM_GAP)))

        # Calculate the expanded cell size that will fill the available width.
        # We subtract SAFETY_MARGIN from the viewport width to prevent the layout
        # engine from dropping the last column due to rounding errors or strict
        # boundary checks.
        cell_size = int((viewport_width - self.SAFETY_MARGIN) / num_cols)
        new_item_width = cell_size - self.ITEM_GAP
        if new_item_width < self.MIN_ITEM_WIDTH:
            return  # Don't update if it would make items too small

        current_size = self.iconSize().width()
        if current_size != new_item_width:
            new_size = QSize(new_item_width, new_item_width)
            self.setIconSize(new_size)
            self.setGridSize(QSize(cell_size, cell_size))

            delegate = self.itemDelegate()
            if hasattr(delegate, "set_base_size"):
                delegate.set_base_size(new_item_width)

    # ------------------------------------------------------------------
    # Timeline rail and date badge
    # ------------------------------------------------------------------
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

    def _on_timeline_scrub(self, row: int, y: int) -> None:
        """Continuous scrubbing: the ball follows the pointer immediately.

        The grid itself loads a new window per jump, which is far slower than a
        mouse move. Moving the dot from the drag position (instead of waiting for
        the loaded rows) keeps the ball glued to the cursor; the grid follows at a
        lower cadence and is corrected exactly when the drag ends.
        """

        self._scrub_pending_row = row
        self._last_hover_row = row
        self._last_scrub_at = time.monotonic()
        setter = getattr(self._timeline_scrubber, "set_current_row", None)
        if callable(setter):
            setter(row)
        text = self._date_text_for_row(row, allow_blocking=False)
        if text:
            self._show_date_badge(text, self._scrubber_y_to_view(y))
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

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            if not self._updating_style:
                self._apply_scrollbar_style()
        super().changeEvent(event)

    def _apply_scrollbar_style(self) -> None:
        # Fetch the global application palette to ensure we get the fresh theme colors,
        # ignoring any local stylesheet overrides that self.palette() might reflect.
        app = QGuiApplication.instance()
        palette = app.palette() if app else self.palette()

        text_color = palette.color(QPalette.ColorRole.WindowText)
        base_color = palette.color(QPalette.ColorRole.Base)

        # Propagate the base colour to the viewport palette so that
        # autoFillBackground paints an opaque surface even when the parent
        # window uses WA_TranslucentBackground.
        vp = self.viewport()
        vp_palette = vp.palette()
        vp_palette.setColor(QPalette.ColorRole.Window, base_color)
        vp_palette.setColor(QPalette.ColorRole.Base, base_color)
        vp.setPalette(vp_palette)

        # Enforce the background color on the QListView so it is painted opaque
        # in translucent/frameless window configurations.
        style = modern_scrollbar_style(text_color)
        bg_style = f"QListView {{ background-color: {base_color.name()}; }}"

        full_style = f"{style}\n{bg_style}"

        if self.styleSheet() == full_style:
            return

        self._updating_style = True
        try:
            self.setStyleSheet(full_style)
        finally:
            self._updating_style = False

    def setModel(self, model) -> None:  # type: ignore[override]
        previous = self.model()
        if previous is not None:
            for signal_name, handler in [
                ("modelReset", self._on_model_reset),
                ("rowsInserted", self._on_rows_inserted),
                ("rowsRemoved", self._update_empty_state),
            ]:
                try:
                    getattr(previous, signal_name).disconnect(handler)
                except (RuntimeError, TypeError):
                    pass
        super().setModel(model)
        if model is not None:
            model.modelReset.connect(self._on_model_reset)
            model.rowsInserted.connect(self._on_rows_inserted)
            model.rowsRemoved.connect(self._update_empty_state)
        self._query_loading = True
        self._update_empty_state()

    def _on_model_reset(self) -> None:
        import logging
        model = self.model()
        rows = model.rowCount() if model is not None else 0
        logging.getLogger(__name__).info(
            "[DIAG] _on_model_reset: rows=%s empty_mode=%s query_loading=%s scan_completed=%s",
            rows, self._empty_mode, self._query_loading, self._scan_completed,
        )
        self._query_loading = True
        # The year rail depends on the model's row layout, so refresh it whenever
        # the source changes.
        self._schedule_timeline_refresh()
        # ⭐ 关键修复：如果 model 已经有数据，立即清除所有 overlay 状态
        # 避免用户看到"加载中"或"无媒体"提示覆盖在照片上
        if model is not None and model.rowCount() > 0:
            self._loading_label.hide()
            self._scanning_label.hide()
            self._empty_label.hide()
            self._query_loading = False
            self._loading_timeout_timer.stop()
            self._empty_state_timer.stop()
            return
        # If an overlay is showing and the model now has rows, dismiss it.
        if self._empty_mode is not None:
            model = self.model()
            if model is not None and model.rowCount() > 0:
                self._empty_label.hide()
                self._empty_mode = None
                self._query_loading = False
                return
        else:
            self._update_empty_state()
        # If no rows appear within 2s the query result is likely empty.
        self._loading_timeout_timer.start(2000)

    def _on_rows_inserted(self) -> None:
        self._query_loading = False
        self._loading_timeout_timer.stop()
        # If a filtered mode was active and rows now exist, hide the overlay.
        if self._empty_mode is not None:
            self._empty_label.hide()
            self._empty_mode = None
        self._update_empty_state()
        self._schedule_timeline_refresh()

    def _on_loading_timeout(self) -> None:
        self._query_loading = False
        # ⭐ 关键修复：如果 model 已经有数据，立即清除所有 overlay
        # 避免 2s 后用户仍然看到"加载中"或"无媒体"提示
        model = self.model()
        if model is not None and model.rowCount() > 0:
            self._loading_label.hide()
            self._scanning_label.hide()
            self._empty_label.hide()
            return
        # If overlay was shown via set_empty_mode but the model now has rows,
        # dismiss the overlay.
        if self._empty_mode is not None:
            if model is not None and model.rowCount() > 0:
                self._empty_label.hide()
                self._empty_mode = None
                return
            # Still empty: overlay stays (text already correct).
            return
        self._do_update_empty_state()

    def set_scan_completed(self) -> None:
        """Mark that the first scan has finished — allows 'No media found' to show."""
        if self._scan_completed and not self._is_scanning:
            return  # already completed
        self._scan_completed = True
        self._is_scanning = False  # 扫描完成
        self._query_loading = False
        self._empty_state_timer.stop()
        self._loading_timeout_timer.stop()
        self._do_update_empty_state()

    def set_scan_started(self) -> None:
        """标记扫描开始 - 显示扫描中状态。"""
        if self._scan_completed:
            return  # don't override completed state
        self._is_scanning = True
        self._empty_state_timer.stop()
        self._do_update_empty_state()

    def set_startup_overlay_active(self, active: bool) -> None:
        """Tell the grid that the window-level startup overlay is in charge.

        While it is, every grid placeholder stays hidden so the user never sees
        a second "准备中..." / "正在扫描照片…" box next to the overlay's own
        message and progress bar.
        """

        self._startup_overlay_active = bool(active)
        if self._startup_overlay_active:
            if self._loading_label is not None:
                self._loading_label.hide()
            if self._scanning_label is not None:
                self._scanning_label.hide()
            if self._empty_label is not None:
                self._empty_label.hide()
        self._do_update_empty_state()

    def dismiss_loading_state(self) -> None:
        """强制清除所有加载/扫描状态，确保照片可见。

        当外部逻辑（如 gallery_page.hide_loading）确认加载已完成时调用，
        清除所有可能残留的加载/扫描图层，让 grid view 立刻显示内容。
        """
        self._is_scanning = False
        self._query_loading = False
        self._scan_completed = True
        self._empty_mode = None
        self._empty_state_timer.stop()
        self._loading_timeout_timer.stop()
        # 强制隐藏所有 overlay 标签
        if self._loading_label is not None:
            self._loading_label.hide()
        if self._scanning_label is not None:
            self._scanning_label.hide()
        if self._empty_label is not None:
            self._empty_label.hide()
        # 刷新显示
        self.viewport().update()

    def _update_empty_state(self) -> None:
        """Debounced version — coalesces rapid model changes."""
        self._empty_state_timer.start()

    def _do_update_empty_state(self) -> None:
        """Actually update the empty state (standard path without overlay)."""
        if self._empty_mode is not None:
            return  # overlay managed by set_empty_mode / _on_rows_inserted
        model = self.model()
        is_empty = model is None or model.rowCount() == 0
        if self._empty_label is None:
            return
        self._empty_label.setGeometry(self.viewport().rect())
        self._loading_label.setGeometry(self.viewport().rect())
        self._scanning_label.setGeometry(self.viewport().rect())

        import logging
        logging.getLogger(__name__).info(
            "[DIAG] _do_update_empty_state: is_empty=%s query_loading=%s scan_completed=%s is_scanning=%s",
            is_empty, self._query_loading, self._scan_completed, self._is_scanning,
        )

        # The window-level startup overlay owns this phase. Drawing the grid's
        # own placeholder here produced two competing progress boxes ("正在打开
        # 图库…" next to "准备中...").
        if self._startup_overlay_active:
            self._empty_label.hide()
            self._loading_label.hide()
            self._scanning_label.hide()
            return

        # 优先级：正在扫描 > 正在加载查询 > 空状态
        if is_empty and self._is_scanning:
            # 正在扫描 - 显示扫描中提示
            self._empty_label.hide()
            self._loading_label.hide()
            self._scanning_label.show()
        elif is_empty and self._query_loading:
            # 正在加载查询
            self._empty_label.hide()
            self._loading_label.show()
            self._scanning_label.hide()
        elif is_empty and not self._scan_completed:
            # 启动扫描尚未完成：由启动遮罩负责反馈，这里保持空白，
            # 否则会在遮罩旁边再叠一个"准备中..."。
            self._empty_label.hide()
            self._loading_label.hide()
            self._scanning_label.hide()
        else:
            # 正常状态或扫描完成
            self._loading_label.hide()
            self._scanning_label.hide()
            # ⭐ 修复：is_empty=False 时也强制隐藏 _empty_label，
            # 避免残留的"无媒体"提示覆盖在照片上
            self._empty_label.setVisible(is_empty)

        # ⭐ 关键修复：如果 model 已经有数据，确保所有图层（包括 _empty_label）
        # 都不会显示在用户面前，覆盖照片
        if not is_empty:
            # 强制隐藏所有 overlay 标签
            self._loading_label.hide()
            self._scanning_label.hide()
            self._empty_label.hide()

    def set_empty_mode(self, mode: str | None) -> None:
        """Set the context for the empty-state message (e.g. 'favorites', 'videos').

        When *mode* is not ``None`` the empty label goes up immediately
        with the appropriate text.  It is hidden again as soon as the
        first row arrives.  Passing ``None`` clears the override so the
        standard empty-state logic resumes.

        The label geometry update is deferred by one event-loop tick so
        the viewport and its parent stack page are fully laid out.
        """
        # ⭐ 立即更新模式，防止竞争条件
        self._empty_mode = mode

        if mode is not None:
            self._empty_label.setText(self._empty_message())
            self._loading_label.hide()
            self._scanning_label.hide()

            def _show() -> None:
                if self._empty_mode != mode:
                    return  # mode changed before timer fired
                vp_rect = self.viewport().rect()
                if vp_rect.width() <= 0 or vp_rect.height() <= 0:
                    vp_rect = self.rect()
                self._empty_label.setGeometry(vp_rect)
                self._empty_label.show()
                self._empty_label.raise_()
                self.viewport().update()

            QTimer.singleShot(0, _show)
        else:
            # ⭐ 清除模式时立即隐藏标签并重置文本
            self._empty_label.hide()
            # ⭐ 重置标签文本为默认值，防止显示旧的错误消息
            self._empty_label.setText("No media found. Click Rescan to scan this library.")
            self._update_empty_state()

    def _empty_message(self) -> str:
        """Return a context-aware empty-state message."""
        mode = self._empty_mode
        if mode == "favorites":
            return "没有收藏"
        if mode == "videos":
            return "没有视频"
        if mode == "live":
            return "没有实况照片"
        if mode == "no_library":
            return "请选择照片文件夹"
        return "没有照片"

    # ------------------------------------------------------------------
    # Selection mode toggling
    # ------------------------------------------------------------------
    def selection_mode_active(self) -> bool:
        """Return ``True`` when multi-selection mode is currently enabled."""

        return self._selection_mode_enabled

    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Switch between the default single selection and multi-selection mode."""

        desired_state = bool(enabled)
        if self._selection_mode_enabled == desired_state:
            return
        self._selection_mode_enabled = desired_state
        if desired_state:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            self.setSelectionRectVisible(True)
        else:
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionRectVisible(False)
        # Long-press previews interfere with multi-selection because the delayed
        # activation steals focus from the selection rubber band. Disabling the
        # preview gesture keeps the pointer interactions unambiguous.
        self.set_preview_enabled(not desired_state)

    # ------------------------------------------------------------------
    # Mouse Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            viewport_pos = self._viewport_pos(event)
            # Check for favorite badge click
            index = self.indexAt(viewport_pos)
            if index.isValid():
                if self._is_favorite_badge_click(index, viewport_pos):
                    self._toggle_favorite(index)
                    return  # Don't propagate (avoids selection/play)

        super().mousePressEvent(event)

    def _is_favorite_badge_click(self, index, pos: QPoint) -> bool:
        # Reconstruct logic from BadgeRenderer.draw_favorite_badge
        rect = self.visualRect(index)
        if not rect.isValid(): return False

        # If rect contains pos, we need to check sub-rect for badge
        # Logic from BadgeRenderer:
        # padding = 5
        # icon_size = 16
        # badge_width = icon_size + padding * 2
        # badge_height = icon_size + padding * 2
        # badge_rect = QRect(
        #     rect.left() + 8,
        #     rect.bottom() - badge_height - 8,
        #     badge_width,
        #     badge_height,
        # )
        padding = 5
        icon_size = 16
        badge_width = icon_size + padding * 2
        badge_height = icon_size + padding * 2

        # Adjust local rect
        badge_rect = QRect(
            rect.left() + 8,
            rect.bottom() - badge_height - 8,
            badge_width,
            badge_height,
        )

        return badge_rect.contains(pos)

    def _toggle_favorite(self, index: QModelIndex) -> None:
        self.favoriteClicked.emit(index)

    favoriteClicked = Signal(QModelIndex)
