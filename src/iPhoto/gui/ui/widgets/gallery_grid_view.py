"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QPalette, QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QListView, QLabel, QStyleOptionViewItem

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid
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
