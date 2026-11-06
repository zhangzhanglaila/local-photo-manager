"""Startup loading overlay shown during application initialization."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Slot
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from ....i18n import tr


class StartupOverlay(QWidget):
    """Semi-transparent overlay with loading text and progress bar.

    Displayed over the main window during startup to indicate that
    the application is loading.  Dismissed once initialization completes.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        # Must exist before the event filter below can run, because a resize
        # event can arrive while this __init__ is still executing.
        self._dismissed = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "background-color: rgba(255, 255, 255, 220);"
        )
        parent.installEventFilter(self)

        # The content lives in one fixed-size block that ``_sync_geometry``
        # centres by hand. Relying on the layout's AlignCenter left the block at
        # the position computed for the first (placeholder) parent size, which
        # painted the message right of centre once the real window size arrived.
        self._content = QWidget(self)
        self._content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._content.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._label = QLabel(tr("startup.starting"), self._content)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "color: #333; font-size: 16px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(self._label)

        # 添加扫描状态提示（用于首次扫描时显示）
        self._scan_label = QLabel("", self._content)
        self._scan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scan_label.setStyleSheet(
            "color: #666; font-size: 13px; background: transparent;"
        )
        self._scan_label.hide()  # 默认隐藏
        layout.addWidget(self._scan_label)

        self._progress = QProgressBar(self._content)
        self._progress.setRange(0, 0)  # indeterminate mode
        self._progress.setFixedWidth(260)
        self._progress.setFixedHeight(6)
        self._progress.setStyleSheet(
            "QProgressBar { border: none; background: #e0e0e0; border-radius: 3px; }"
            "QProgressBar::chunk { background: #4a90d9; border-radius: 3px; }"
        )
        layout.addWidget(self._progress)

        self.hide()

    def _is_alive(self) -> bool:
        """Return True if the underlying Qt widget is still valid."""
        if self._dismissed:
            return False
        # After deleteLater() the C++ object is gone but the Python wrapper
        # remains; accessing it raises RuntimeError.  Try a cheap attribute
        # first to detect this without crashing.
        try:
            _ = self.isVisible()
        except RuntimeError:
            return False
        return True

    def set_message(self, text: str) -> None:
        """Update the loading message."""
        if not self._is_alive():
            return
        try:
            self._label.setText(text)
            self._sync_geometry()  # the block width can change with the text
        except RuntimeError:
            # Underlying C++ object was deleted between the guard and now.
            self._dismissed = True

    def show_scan_progress(self, current: int, total: int) -> None:
        """显示扫描进度。

        Args:
            current: 当前已处理的文件数
            total: 总文件数（-1 表示未知）
        """
        if not self._is_alive():
            return
        try:
            self._scan_label.show()
            if total > 0 and total != -1:
                # 显示具体进度
                percentage = int((current / total) * 100) if total > 0 else 0
                self._scan_label.setText(f"正在扫描照片... {current}/{total} ({percentage}%)")
                self._progress.setRange(0, total)
                self._progress.setValue(current)
            else:
                # 显示不确定进度
                self._scan_label.setText(f"正在扫描照片... 已处理 {current} 个文件")
                self._progress.setRange(0, 0)  # 不确定模式
            self._sync_geometry()  # showing the scan label widens the block
        except RuntimeError:
            self._dismissed = True

    def _sync_geometry(self) -> None:
        """Cover the parent and centre the content block inside it.

        ``show_overlay()`` runs before the window is laid out, so the parent can
        still report a placeholder size (100x30) and is resized again later.
        Both the cover rect and the content position are therefore recomputed
        from scratch every time instead of trusting the layout's alignment.
        """

        if not self._is_alive():
            return
        parent = self.parentWidget()
        if parent is None:
            return
        try:
            self.setGeometry(0, 0, parent.width(), parent.height())
            self._content.adjustSize()
            self._content.move(
                max(0, (self.width() - self._content.width()) // 2),
                max(0, (self.height() - self._content.height()) // 2),
            )
            self.update()
        except RuntimeError:
            self._dismissed = True

    def show_overlay(self) -> None:
        """Show the overlay and resize to match parent."""
        if not self._is_alive():
            return
        try:
            self._sync_geometry()
            self.raise_()
            self.show()
            # The parent is laid out after this call, so re-sync once the
            # pending layout work has run.
            QTimer.singleShot(0, self._sync_geometry)
        except RuntimeError:
            self._dismissed = True

    def dismiss(self) -> None:
        """Hide and delete the overlay."""
        if self._dismissed:
            return  # idempotent — second call is a no-op
        self._dismissed = True
        try:
            self.hide()
            self.deleteLater()
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if (
            not self._dismissed
            and obj is self.parentWidget()
            and event.type() in (QEvent.Type.Resize, QEvent.Type.Show)
        ):
            self._sync_geometry()
        return super().eventFilter(obj, event)
