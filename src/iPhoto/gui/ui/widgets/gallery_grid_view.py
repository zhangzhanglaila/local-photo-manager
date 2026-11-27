"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QStyleOptionViewItem,
)

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid
from .gallery_timeline import GalleryTimelineMixin
from ....i18n import tr
from ...viewmodels.gallery_empty_state import GalleryEmptyState, GalleryEmptyStateViewModel
from ....utils.logging import diag


class GalleryGridView(GalleryTimelineMixin, AssetGrid):
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selection_mode_enabled = False
        self._placeholder_label = None
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

        # One placeholder label: the grid only ever shows one message at a time,
        # and which one is decided by ``GalleryEmptyStateViewModel``.
        self._placeholder_label = QLabel(tr("empty.no_media"), vp)
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setWordWrap(True)
        self._placeholder_label.setStyleSheet("color: #86868b; font-size: 15px;")
        self._placeholder_label.hide()

        # Last viewport size the placeholder was sized to. It spans the whole
        # viewport, so re-applying its geometry on every state update repainted the
        # entire grid and made timeline dragging crawl.
        self._placeholder_rect = None

        # Scan/query progress for the placeholder. The view never inspects the
        # individual flags: it renders the single state the model derives.
        self._empty = GalleryEmptyStateViewModel()

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
    def paintEvent(self, event: QPaintEvent) -> None:
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return

        if self._placeholder_label is not None:
            self._placeholder_label.setGeometry(self.viewport().rect())

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
    def _store(self):
        """Return the gallery store behind the model, when there is one."""

        model = self.model()
        return getattr(model, "_store", None)

    def _set_load_apply_throttle(self, seconds: float) -> None:
        store = self._store()
        setter = getattr(store, "set_apply_throttle", None)
        if callable(setter):
            setter(seconds)

    def _set_merge_on_apply(self, merge: bool) -> None:
        store = self._store()
        setter = getattr(store, "set_merge_on_apply", None)
        if callable(setter):
            setter(merge)

    def _set_scan_refresh_suppressed(self, suppressed: bool) -> None:
        store = self._store()
        setter = getattr(store, "set_scan_refresh_suppressed", None)
        if callable(setter):
            setter(suppressed)

    def _flush_deferred_load(self) -> None:
        store = self._store()
        flush = getattr(store, "flush_deferred_load", None)
        if callable(flush):
            flush()


    @staticmethod
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

    def setModel(self, model) -> None:
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
        self._empty.query_started()
        self._update_empty_state()

    def _on_model_reset(self) -> None:
        model = self.model()
        rows = model.rowCount() if model is not None else 0
        diag(
            "[DIAG] _on_model_reset: rows=%s mode=%s",
            rows, self._empty.mode,
        )
        self._empty.query_started()
        # The year rail depends on the model's row layout, so refresh it whenever
        # the source changes.
        self._schedule_timeline_refresh()
        # ⭐ 关键修复：如果 model 已经有数据，立即清除所有 overlay 状态
        # 避免用户看到"加载中"或"无媒体"提示覆盖在照片上
        if rows > 0:
            # Rows exist, so a filter's empty message no longer applies; leaving it
            # set let it re-appear on the next view.
            self._empty.rows_arrived()
            self._hide_placeholder()
            self._empty_state_timer.stop()
            self._loading_timeout_timer.stop()
            return
        # If a filter context is pending, the message stays hidden until the rows
        # arrive (or the safety timer proves the result genuinely empty).
        if self._empty.mode is None:
            self._update_empty_state()
        # If no rows appear within 2s the query result is likely empty.
        self._loading_timeout_timer.start(2000)

    def _on_rows_inserted(self) -> None:
        self._empty.rows_arrived()
        self._loading_timeout_timer.stop()
        # Rows must never wait for the debounce to uncover the thumbnails.
        self._hide_placeholder()
        self._update_empty_state()
        self._schedule_timeline_refresh()

    def _on_loading_timeout(self) -> None:
        self._empty.query_finished()
        # ⭐ 关键修复：如果 model 已经有数据，立即清除所有 overlay
        # 避免 2s 后用户仍然看到"加载中"或"无媒体"提示
        model = self.model()
        if model is not None and model.rowCount() > 0:
            self._hide_placeholder()
            return
        self._do_update_empty_state()

    def set_scan_completed(self) -> None:
        """Mark that the first scan has finished — allows 'No media found' to show."""
        if not self._empty.scan_finished():
            return  # already completed
        self._empty_state_timer.stop()
        self._loading_timeout_timer.stop()
        self._do_update_empty_state()

    def set_scan_started(self) -> None:
        """标记扫描开始 - 显示扫描中状态。

        这同样适用于「首次扫描已完成之后」的重新扫描：新增图库根目录或相册时，
        该相册在索引写入完成前是空的，此时必须显示"正在扫描"，而不是
        "No media found. Click Rescan"，否则用户会以为所选文件夹没有照片。
        """
        self._empty.scan_started()
        self._empty_state_timer.stop()
        self._do_update_empty_state()

    def dismiss_loading_state(self) -> None:
        """强制清除所有加载/扫描状态，确保照片可见。

        当外部逻辑（如 gallery_page.hide_loading）确认加载已完成时调用，
        清除所有可能残留的加载/扫描图层，让 grid view 立刻显示内容。
        """
        self._empty.dismiss_loading()
        self._empty_state_timer.stop()
        self._loading_timeout_timer.stop()
        self._hide_placeholder()
        # 刷新显示
        self.viewport().update()

    def _update_empty_state(self) -> None:
        """Debounced version — coalesces rapid model changes."""
        self._empty_state_timer.start()

    def _current_empty_state(self) -> GalleryEmptyState:
        """Derive the placeholder for the model's current row count."""

        model = self.model()
        return self._empty.state(model is not None and model.rowCount() > 0)

    def _hide_placeholder(self) -> None:
        """Hide the placeholder without changing the derived state."""

        label = self._placeholder_label
        if label is not None:
            label.hide()

    def _do_update_empty_state(self) -> None:
        """Actually update the empty state."""
        label = self._placeholder_label
        if label is None:
            return
        # Only when the viewport actually changed: the label covers the whole view,
        # so re-applying its geometry on every update forced a full repaint.
        viewport_rect = self.viewport().rect()
        if viewport_rect != self._placeholder_rect:
            self._placeholder_rect = viewport_rect
            label.setGeometry(viewport_rect)

        state = self._current_empty_state()
        diag(
            "[DIAG] _do_update_empty_state: state=%s mode=%s",
            state.value, self._empty.mode,
        )
        self._apply_empty_state(state)

    def _apply_empty_state(self, state: GalleryEmptyState) -> None:
        """Render one derived state with a single visibility decision."""

        label = self._placeholder_label
        if label is None:
            return
        if state is GalleryEmptyState.SCANNING:
            # 正在扫描：首次启动时这层会被启动遮罩盖住，不可见；
            # 运行中追加根目录/相册的重扫则直接呈现给用户。
            label.setText(tr("empty.scanning"))
        elif state is GalleryEmptyState.LOADING:
            # 正在加载查询
            label.setText(tr("preview.loading"))
        elif state is GalleryEmptyState.EMPTY:
            label.setText(self._empty.message())
        else:
            # READY / INITIALIZING. 启动扫描尚未完成时由启动遮罩负责反馈，
            # 这里保持空白，否则会在遮罩旁边再叠一个"准备中..."；有数据时
            # 则任何占位提示都不能盖住照片。
            label.hide()
            return
        label.show()

    def retranslate(self) -> None:
        """Re-apply the placeholder texts after a language switch."""

        self._apply_empty_state(self._current_empty_state())

    def set_empty_mode(self, mode: str | None) -> None:
        """Record the filter context for the empty-state text (videos/live/...).

        The text used to be painted straight away, so switching to 视频 or 实况
        flashed "没有视频" for a moment before the rows arrived. The mode is only
        recorded here; the label appears when the query is done *and* the result
        is genuinely empty, which is what ``_do_update_empty_state`` decides.
        """

        self._empty.set_mode(mode)
        label = self._placeholder_label
        if label is not None:
            label.setText(self._empty.message())
            # Hide rather than show: an empty result is not known yet.
            label.hide()
        self._update_empty_state()

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
