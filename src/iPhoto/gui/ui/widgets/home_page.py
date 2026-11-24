"""Minimal home page: a large wordmark and one line of copy on an empty canvas."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

ACCENT = "#7b5e3b"
MUTED = "#9a948a"


class HomePage(QWidget):
    """Landing view shown before any library section is opened."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        self.setStyleSheet("QWidget#homePage { background: #fbfaf8; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(10)
        layout.addStretch(1)

        wordmark = QLabel("iphoto", self)
        wordmark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wordmark.setStyleSheet(
            f"font-size: 64px; font-weight: 300; letter-spacing: 6px; color: {ACCENT};"
        )
        layout.addWidget(wordmark)

        tagline = QLabel("每一张照片，都是一段值得回看的时光", self)
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet(f"font-size: 15px; color: {MUTED}; letter-spacing: 2px;")
        layout.addWidget(tagline)

        layout.addStretch(1)
