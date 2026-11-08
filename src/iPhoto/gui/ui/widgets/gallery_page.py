"""Gallery page embedding the grid view inside a simple layout."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from ..icon import load_icon
from .gallery_grid_view import GalleryGridView
from .main_window_metrics import HEADER_BUTTON_SIZE, HEADER_ICON_GLYPH_SIZE


class _LoadingSpinner(QWidget):
    """Small indeterminate spinner in the platform accent colour."""

    def __init__(self, diameter: int = 28, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)

    def _advance(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self.palette().color(QPalette.ColorRole.Highlight))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect().adjusted(3, 3, -3, -3)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)


_SECONDARY_BUTTON_STYLE = (
    "QPushButton { padding: 5px 14px; font-size: 12px; border-radius: 6px;"
    " background: palette(button); color: palette(button-text);"
    " border: 1px solid palette(mid); }"
    "QPushButton:hover { background: palette(midlight); }"
    "QPushButton:pressed { background: palette(mid); }"
)


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
        self._search_results_label.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: palette(text);"
        )
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

        # Loading timer (ETA estimate only; elapsed seconds are not shown).
        self._loading_start_time = 0
        self._loading_phase = ""

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
        top_bar_layout.addStretch()

        layout.addWidget(top_bar)

        # Content area — a narrow centred column, macOS-style status.
        content = QWidget()
        content.setFixedWidth(460)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._spinner = _LoadingSpinner(32)
        content_layout.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignHCenter)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: palette(text);"
        )
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setFixedWidth(460)
        self._status_label.setContentsMargins(0, 14, 0, 0)
        content_layout.addWidget(self._status_label)

        self._sub_label = QLabel("")
        self._sub_label.setStyleSheet(
            "font-size: 12px; color: palette(placeholder-text);"
        )
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_label.setWordWrap(True)
        self._sub_label.setFixedWidth(460)
        self._sub_label.setContentsMargins(0, 5, 0, 0)
        content_layout.addWidget(self._sub_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedWidth(320)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            "QProgressBar { border: none; background: palette(midlight);"
            " border-radius: 2px; }"
            "QProgressBar::chunk { background: palette(highlight);"
            " border-radius: 2px; }"
        )

        self._detail_label = QLabel("")
        self._detail_label.setStyleSheet(
            "font-size: 11px; color: palette(placeholder-text);"
        )
        self._detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._progress_widget = QWidget()
        progress_layout = QVBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 16, 0, 0)
        progress_layout.setSpacing(6)
        progress_layout.addWidget(
            self._progress_bar, 0, Qt.AlignmentFlag.AlignHCenter
        )
        progress_layout.addWidget(self._detail_label)
        self._progress_widget.hide()
        content_layout.addWidget(
            self._progress_widget, 0, Qt.AlignmentFlag.AlignHCenter
        )

        self._continue_btn = QPushButton("先去看照片")
        self._continue_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
        self._continue_btn.setMinimumHeight(30)
        self._continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._continue_btn.clicked.connect(self._on_continue_clicked)
        self._continue_btn.hide()

        self._retry_btn = QPushButton("重试")
        self._retry_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
        self._retry_btn.setMinimumHeight(30)
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.clicked.connect(self._on_retry_clicked)
        self._retry_btn.hide()

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 20, 0, 0)
        btn_layout.setSpacing(8)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        btn_layout.addWidget(self._continue_btn)
        btn_layout.addWidget(self._retry_btn)
        content_layout.addLayout(btn_layout)

        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet(
            "font-size: 11px; color: palette(placeholder-text);"
        )
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setContentsMargins(0, 10, 0, 0)
        self._hint_label.hide()
        content_layout.addWidget(self._hint_label)

        layout.addStretch(1)
        layout.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        return widget

    # --- Public API ---

    def show_search_loading(self, query: str) -> None:
        """Show search loading state."""
        self._spinner.show()
        self._spinner.start()
        self._status_label.setText(f"正在搜索“{query}”")
        self._sub_label.setText("使用本地 AI 搜索照片")
        self._progress_widget.hide()
        self._continue_btn.hide()
        self._retry_btn.hide()
        self._hint_label.hide()
        self._loading_phase = "search"
        self._start_timer()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_model_loading(self, elapsed: int = 0) -> None:
        """Show model loading phase."""
        self._spinner.show()
        self._spinner.start()
        self._status_label.setText("正在准备搜索")
        self._sub_label.setText("正在加载本地 AI 模型，只需一次")
        self._progress_widget.hide()
        self._continue_btn.show()
        self._retry_btn.hide()
        self._hint_label.setText("之后的搜索会很快")
        self._hint_label.show()
        self._loading_phase = "model"
        self._start_timer()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_indexing_progress(self, current: int, total: int) -> None:
        """Show indexing progress with a thin progress bar."""
        self._spinner.show()
        self._spinner.start()
        self._status_label.setText("正在建立搜索索引")
        self._sub_label.setText(f"已处理 {current} / {total} 张照片")

        pct = int(current / total * 100) if total > 0 else 0
        self._progress_bar.setMaximum(max(1, total))
        self._progress_bar.setValue(current)
        self._progress_widget.show()

        # ETA is measured from the start of the indexing phase; repeated progress
        # updates must not reset it.
        if self._loading_phase != "index":
            self._loading_phase = "index"
            self._start_timer()
        eta_text = self._calculate_eta(current, total)
        self._detail_label.setText(f"{pct}% · {eta_text}")
        self._detail_label.show()

        self._continue_btn.show()
        self._retry_btn.hide()
        self._hint_label.hide()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_error(self, title: str, message: str) -> None:
        """Show error state with retry button."""
        self._spinner.stop()
        self._spinner.hide()
        self._status_label.setText(title)
        self._sub_label.setText(message)
        self._progress_widget.hide()
        self._continue_btn.show()
        self._retry_btn.show()
        self._hint_label.hide()
        self._loading_phase = "error"
        self._stop_timer()
        self._stack.setCurrentWidget(self._loading_widget)

    def show_done(self, count: int) -> None:
        """Show completion and switch to grid."""
        self.hide_loading()

    def hide_loading(self) -> None:
        """Hide loading view and show grid."""
        self._stop_timer()
        self._loading_phase = ""
        self._spinner.stop()
        self._progress_widget.hide()
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
        self._search_results_label.setText(f"搜索“{query}” · {count} 张")
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
        """Record when the current loading phase started (used for the ETA)."""
        self._loading_start_time = time.time()

    def _stop_timer(self) -> None:
        """Reset the loading start time."""
        self._loading_start_time = 0

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
