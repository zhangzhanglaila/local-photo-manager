"""Gallery page embedding the grid view inside a simple layout."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from ..icon import load_icon
from .gallery_grid_view import GalleryGridView
from .main_window_metrics import HEADER_BUTTON_SIZE, HEADER_ICON_GLYPH_SIZE


class GalleryPageWidget(QWidget):
    """Thin wrapper that exposes the gallery grid view as a self-contained page."""

    backRequested = Signal()
    searchBackRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("galleryPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header with optional back button for cluster gallery mode
        self._header = QWidget()
        self._header.setObjectName("galleryHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_layout.setSpacing(8)

        self.back_button = QToolButton()
        self.back_button.setObjectName("galleryBackButton")
        self.back_button.setIcon(load_icon("chevron.left.svg"))
        self.back_button.setIconSize(HEADER_ICON_GLYPH_SIZE)
        self.back_button.setFixedSize(HEADER_BUTTON_SIZE)
        self.back_button.setAutoRaise(True)
        self.back_button.setToolTip("Return to Map")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.backRequested.emit)
        header_layout.addWidget(self.back_button)
        header_layout.addStretch()

        self._header.hide()
        layout.addWidget(self._header)

        # Search results header (hidden by default)
        self._search_results_header = QWidget()
        search_header_layout = QHBoxLayout(self._search_results_header)
        search_header_layout.setContentsMargins(8, 8, 8, 8)

        self._search_back_btn = QToolButton()
        self._search_back_btn.setIcon(load_icon("chevron.left.svg"))
        self._search_back_btn.setIconSize(HEADER_ICON_GLYPH_SIZE)
        self._search_back_btn.setFixedSize(HEADER_BUTTON_SIZE)
        self._search_back_btn.setAutoRaise(True)
        self._search_back_btn.setToolTip("返回照片")
        self._search_back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_back_btn.clicked.connect(self._on_search_back_clicked)
        search_header_layout.addWidget(self._search_back_btn)

        self._search_results_label = QLabel("搜索结果")
        self._search_results_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        search_header_layout.addWidget(self._search_results_label)
        search_header_layout.addStretch()

        self._search_results_header.hide()
        layout.addWidget(self._search_results_header)

        # Stacked widget: grid view + loading view
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Grid view (index 0)
        self.grid_view = GalleryGridView()
        self.grid_view.setObjectName("galleryGridView")
        self._stack.addWidget(self.grid_view)

        # Loading view (index 1)
        self._loading_widget = self._create_loading_view()
        self._stack.addWidget(self._loading_widget)

        self._stack.setCurrentWidget(self.grid_view)

        # Timer state
        self._loading_start_time = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)

    def _create_loading_view(self) -> QWidget:
        """Create the loading view with status, progress, and buttons."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar with back button
        top_bar = QWidget()
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(8, 8, 8, 8)

        self._search_back_button = QToolButton()
        self._search_back_button.setIcon(load_icon("chevron.left.svg"))
        self._search_back_button.setIconSize(HEADER_ICON_GLYPH_SIZE)
        self._search_back_button.setFixedSize(HEADER_BUTTON_SIZE)
        self._search_back_button.setAutoRaise(True)
        self._search_back_button.setToolTip("返回照片")
        self._search_back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_back_button.clicked.connect(self._on_back_clicked)
        top_bar_layout.addWidget(self._search_back_button)

        self._search_title = QLabel("搜索中")
        self._search_title.setStyleSheet("font-size: 14px; font-weight: bold;")
        top_bar_layout.addWidget(self._search_title)
        top_bar_layout.addStretch()

        layout.addWidget(top_bar)

        # Content area
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(60, 20, 60, 40)
        content_layout.setSpacing(16)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon
        self._icon_label = QLabel("⏳")
        self._icon_label.setStyleSheet("font-size: 48px;")
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._icon_label)

        # Main status
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: palette(text);")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        content_layout.addWidget(self._status_label)

        # Sub status
        self._sub_label = QLabel("")
        self._sub_label.setStyleSheet("font-size: 13px; color: palette(mid);")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_label.setWordWrap(True)
        content_layout.addWidget(self._sub_label)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setMinimumWidth(400)
        self._progress_bar.setMaximumWidth(500)
        self._progress_bar.setFixedHeight(24)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        self._progress_bar.hide()
        content_layout.addWidget(self._progress_bar, 0, Qt.AlignmentFlag.AlignCenter)

        # Progress detail (current/total + ETA)
        self._detail_label = QLabel("")
        self._detail_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        self._detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_label.hide()
        content_layout.addWidget(self._detail_label)

        # Elapsed time
        self._time_label = QLabel("")
        self._time_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._time_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._continue_btn = QPushButton("先去看照片")
        self._continue_btn.setStyleSheet(
            "QPushButton { padding: 10px 24px; font-size: 13px; "
            "background: palette(highlight); color: palette(highlighted-text); "
            "border-radius: 8px; font-weight: bold; }"
            "QPushButton:hover { opacity: 0.85; }"
        )
        self._continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._continue_btn.clicked.connect(self._on_continue_clicked)
        self._continue_btn.hide()
        btn_layout.addWidget(self._continue_btn)

        self._retry_btn = QPushButton("重试")
        self._retry_btn.setStyleSheet(
            "QPushButton { padding: 10px 24px; font-size: 13px; "
            "background: palette(button); color: palette(button-text); "
            "border-radius: 8px; }"
            "QPushButton:hover { background: palette(mid); }"
        )
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.clicked.connect(self._on_retry_clicked)
        self._retry_btn.hide()
        btn_layout.addWidget(self._retry_btn)

        content_layout.addLayout(btn_layout)

        # Hint
        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet("font-size: 11px; color: palette(mid); font-style: italic;")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._hint_label)

        layout.addWidget(content, 1)

        return widget

    # --- Public API ---

    def show_search_loading(self, query: str) -> None:
        """Show search loading state."""
        self._search_title.setText(f"搜索: {query}")
        self._icon_label.setText("🔍")
        self._status_label.setText(f"正在搜索 \"{query}\"")
        self._sub_label.setText("正在使用 AI 搜索照片...")
        self._progress_bar.hide()
        self._detail_label.hide()
        self._continue_btn.hide()
        self._retry_btn.hide()
        self._hint_label.hide()
        self._start_timer()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_model_loading(self, elapsed: int = 0) -> None:
        """Show model loading phase."""
        self._search_title.setText("初始化中")
        self._icon_label.setText("⏳")
        self._status_label.setText("正在加载 AI 模型（约350MB）")
        self._sub_label.setText("首次使用需要加载模型，之后搜索会很快")
        self._progress_bar.hide()
        self._detail_label.hide()
        self._continue_btn.show()
        self._retry_btn.hide()
        self._hint_label.setText("💡 之后搜索只需 0.1 秒")
        self._hint_label.show()
        self._start_timer()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_indexing_progress(self, current: int, total: int) -> None:
        """Show indexing progress with progress bar."""
        self._search_title.setText("初始化中")
        self._icon_label.setText("📸")
        self._status_label.setText("正在为照片生成索引")

        pct = int(current / total * 100) if total > 0 else 0
        self._sub_label.setText(f"已处理 {current} / {total} 张照片")

        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._progress_bar.show()

        # Calculate ETA
        eta_text = self._calculate_eta(current, total)
        self._detail_label.setText(f"{pct}% 完成  •  {eta_text}")
        self._detail_label.show()

        self._continue_btn.show()
        self._retry_btn.hide()
        self._hint_label.setText("💡 之后搜索只需 0.1 秒")
        self._hint_label.show()

        self._stack.setCurrentWidget(self._loading_widget)

    def show_error(self, title: str, message: str) -> None:
        """Show error state with retry button."""
        self._search_title.setText("出错了")
        self._icon_label.setText("❌")
        self._status_label.setText(title)
        self._sub_label.setText(message)
        self._progress_bar.hide()
        self._detail_label.hide()
        self._continue_btn.show()
        self._retry_btn.show()
        self._hint_label.hide()
        self._stop_timer()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_done(self, count: int) -> None:
        """Show completion and switch to grid."""
        self.hide_loading()

    def hide_loading(self) -> None:
        """Hide loading view and show grid."""
        self._stop_timer()
        self._progress_bar.hide()
        self._detail_label.hide()
        # ⭐ 强制切换到 grid_view，加载完成后立即显示照片
        # 即使 _loading_widget 因某种原因被显示，也强制回到 grid_view
        if self._stack.currentWidget() is not self.grid_view:
            self._stack.setCurrentWidget(self.grid_view)
        # ⭐ 通知 grid view 清除加载状态
        if hasattr(self.grid_view, "dismiss_loading_state"):
            self.grid_view.dismiss_loading_state()

    def show_search_results_header(self, query: str, count: int) -> None:
        """Show search results header with back button.

        Args:
            query: The search query.
            count: Number of results.
        """
        self._search_results_label.setText(f"搜索 \"{query}\" - {count} 个结果")
        self._search_results_header.show()

    def hide_search_results_header(self) -> None:
        """Hide search results header."""
        self._search_results_header.hide()

    def _on_search_back_clicked(self) -> None:
        """Handle back button click from search results."""
        self.hide_search_results_header()
        self.searchBackRequested.emit()

    # --- Internal ---

    def _start_timer(self) -> None:
        """Start elapsed time timer."""
        self._loading_start_time = time.time()
        self._time_label.setText("已等待 0 秒")
        self._time_label.show()
        self._elapsed_timer.start(1000)

    def _stop_timer(self) -> None:
        """Stop elapsed time timer."""
        self._elapsed_timer.stop()
        self._loading_start_time = 0

    def _update_elapsed_time(self) -> None:
        """Update elapsed time display."""
        if self._loading_start_time > 0:
            elapsed = int(time.time() - self._loading_start_time)
            if elapsed < 60:
                self._time_label.setText(f"已等待 {elapsed} 秒")
            else:
                minutes = elapsed // 60
                seconds = elapsed % 60
                self._time_label.setText(f"已等待 {minutes} 分 {seconds} 秒")

    def _calculate_eta(self, current: int, total: int) -> str:
        """Calculate estimated time remaining."""
        if current <= 0 or self._loading_start_time <= 0:
            return "计算中..."

        elapsed = time.time() - self._loading_start_time
        if elapsed < 2:
            return "计算中..."

        rate = current / elapsed  # items per second
        remaining = (total - current) / rate if rate > 0 else 0

        if remaining < 60:
            return f"预计还需约 {int(remaining)} 秒"
        elif remaining < 3600:
            minutes = int(remaining // 60)
            return f"预计还需约 {minutes} 分钟"
        else:
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            return f"预计还需约 {hours} 小时 {minutes} 分钟"

    def _on_back_clicked(self) -> None:
        """Handle back button click."""
        self.searchBackRequested.emit()
        self.hide_loading()

    def _on_continue_clicked(self) -> None:
        """Handle continue browsing button click."""
        self.searchBackRequested.emit()
        self.hide_loading()

    def _on_retry_clicked(self) -> None:
        """Handle retry button click."""
        self.searchBackRequested.emit()
        self.hide_loading()

    def set_cluster_gallery_mode(self, enabled: bool, back_tooltip: str = "Return") -> None:
        """Show or hide the header with back button for cluster gallery mode."""
        self.back_button.setToolTip(back_tooltip)
        self._header.setVisible(enabled)


__all__ = ["GalleryPageWidget"]
