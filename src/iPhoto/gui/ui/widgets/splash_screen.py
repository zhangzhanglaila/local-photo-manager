"""Branded splash shown while the application imports and opens its library.

The heavy modules (Qt plugins, torch/CLIP, ONNX) are imported lazily by the launcher
*after* this window is on screen, so the user sees something meaningful during the cold
start instead of a blank desktop and a spinner in the title bar.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

ACCENT = "#7b5e3b"
PANEL = "#faf8f5"


def _icon_path() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates += [Path(local) / "iPhoto" / "app.png", Path(local) / "iPhoto" / "app.ico"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class SplashScreen(QWidget):
    """Frameless branded splash: icon, product name, status line, thin progress bar."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(460, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("splashCard")
        card.setStyleSheet(
            f"QWidget#splashCard {{ background: white; border-radius: 16px; }}"
        )
        inner = QVBoxLayout(card)
        inner.setContentsMargins(36, 34, 36, 26)
        inner.setSpacing(10)

        self._logo = QLabel(card)
        self._logo.setFixedHeight(64)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        icon = _icon_path()
        if icon is not None:
            pixmap = QPixmap(str(icon))
            if not pixmap.isNull():
                self._logo.setPixmap(
                    pixmap.scaled(
                        64,
                        64,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        inner.addWidget(self._logo)

        name = QLabel("iPhoto", card)
        name.setStyleSheet("font-size: 26px; font-weight: 700; color: #1f1f1f;")
        inner.addWidget(name)

        tagline = QLabel("本地照片库", card)
        tagline.setStyleSheet(f"font-size: 13px; color: {ACCENT}; font-weight: 600;")
        inner.addWidget(tagline)

        inner.addSpacing(10)

        self._status = QLabel("正在启动…", card)
        self._status.setStyleSheet("font-size: 12px; color: #8a857c;")
        inner.addWidget(self._status)

        self._bar = QProgressBar(card)
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {PANEL}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}"
        )
        inner.addWidget(self._bar)

        footer = QLabel("数据仅在本机处理，不上传服务器", card)
        footer.setStyleSheet("font-size: 11px; color: #a09a90;")
        inner.addWidget(footer)

        layout.addWidget(card)
        self._center()

    def _center(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        geometry = self.frameGeometry()
        geometry.moveCenter(screen.availableGeometry().center())
        self.move(geometry.topLeft())

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawRoundedRect(self.rect().adjusted(4, 6, -4, -2), 18, 18)
        painter.end()
