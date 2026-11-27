"""Album card widgets used by :class:`AlbumsDashboard`.

The rounded cover view and the card frame paint the album grid; keeping them
here leaves the dashboard free to manage loading, menus and refresh.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..icon import load_icon
from ..theme_manager import DARK_THEME


class RoundedImageView(QWidget):
    """Widget that draws a pixmap clipped to a rounded shape (left side only)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(80, 80)
        self._pixmap: QPixmap | None = None
        self._placeholder: QPixmap | None = None
        self._bg_color = QColor("#B0BEC5")

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def setPlaceholder(self, pixmap: QPixmap) -> None:
        self._placeholder = pixmap
        self.update()

    def set_background_color(self, color: QColor) -> None:
        self._bg_color = color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        path = QPainterPath()
        r = 12.0
        w = float(self.width())
        h = float(self.height())

        # Draw shape: Left side rounded, right side straight
        path.moveTo(w, 0)
        path.lineTo(w, h)
        path.lineTo(r, h)
        path.quadTo(0, h, 0, h - r)
        path.lineTo(0, r)
        path.quadTo(0, 0, r, 0)
        path.closeSubpath()

        painter.setClipPath(path)

        if self._pixmap and not self._pixmap.isNull():
            # Scale cover to fill
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Background
            painter.fillPath(path, self._bg_color)
            # Placeholder icon centered
            if self._placeholder and not self._placeholder.isNull():
                px = (self.width() - self._placeholder.width()) // 2
                py = (self.height() - self._placeholder.height()) // 2
                painter.drawPixmap(px, py, self._placeholder)



class AlbumCard(QFrame):
    """Card widget representing a single album."""

    clicked = Signal(Path)
    menuRequested = Signal(Path, object)

    def __init__(
        self,
        path: Path,
        title: str,
        count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.title = title
        self.setMouseTracking(True)
        self._cursor_pos: QPoint | None = None

        # 1. Container dimensions
        self.setFixedSize(260, 80)
        self.setObjectName("AlbumCard")

        # 2. Layout
        self.layout = QHBoxLayout(self)  # type: ignore[assignment]
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 3. Left side: Image
        self.image_view = RoundedImageView(self)
        self.image_view.setObjectName("ImagePart")
        # Placeholder icon or text until image loads
        self.image_view.setPlaceholder(
            load_icon("photo.on.rectangle", color="#FFFFFF").pixmap(32, 32)
        )

        # 4. Right side: Metadata
        self.text_container = QWidget()
        self.text_container.setObjectName("TextPart")
        self.text_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.text_layout = QVBoxLayout(self.text_container)
        self.text_layout.setContentsMargins(15, 0, 10, 0)
        self.text_layout.setSpacing(4)
        self.text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Title
        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            "color: #1d1d1f; font-size: 14px; font-weight: 600; background: transparent;"
        )
        self.set_title(title)

        # Count
        self.count_label = QLabel(str(count))
        self.count_label.setStyleSheet(
            "color: #86868b; font-size: 13px; background: transparent;"
        )

        self.text_layout.addWidget(self.title_label)
        self.text_layout.addWidget(self.count_label)

        self.layout.addWidget(self.image_view)
        self.layout.addWidget(self.text_container)

        self._base_color = QColor("#F5F5F7")
        self._hover_center_color = QColor("#FFFFFF")
        self._hover_outer_color = QColor("#F5F5F7")

        # 5. Stylesheet
        # Note: Background color is handled in paintEvent for the light source effect
        self.setStyleSheet("""
            /* Parent container: rounded corners handled in paintEvent */
            #AlbumCard {
                border-radius: 12px;
            }

            /* Right text part: transparent */
            #TextPart {
                background-color: transparent;
            }
        """)

        # 6. Shadow
        self.add_shadow()
        self._apply_theme()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_theme()
        super().changeEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._cursor_pos = event.position().toPoint()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor_pos = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menuRequested.emit(self.path, event.globalPos())
        event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(self.rect(), 12, 12)

        if self._cursor_pos:
            # Highlight effect: Radial gradient from cursor
            gradient = QRadialGradient(self._cursor_pos, 200)
            gradient.setColorAt(0.0, self._hover_center_color)
            gradient.setColorAt(1.0, self._hover_outer_color)
            painter.fillPath(path, QBrush(gradient))
        else:
            # Default state
            painter.fillPath(path, self._base_color)

    def add_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 25))
        self.setGraphicsEffect(shadow)

    def set_title(self, title: str) -> None:
        """Set the title with truncation if it exceeds 25 characters."""
        if len(title) > 25:
            truncated = title[:25] + "..."
            self.title_label.setText(truncated)
            self.title_label.setToolTip(title)
        else:
            self.title_label.setText(title)
            self.title_label.setToolTip("")

    def set_cover_image(self, pixmap: QPixmap) -> None:
        """Update the cover image."""
        self.image_view.setPixmap(pixmap)

    def apply_theme(self) -> None:
        """Refresh colors from the active application palette."""
        self._apply_theme()

    def _apply_theme(self) -> None:
        app = QGuiApplication.instance()
        palette = app.palette() if app else self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        text_primary = palette.color(QPalette.ColorRole.Text)

        is_dark = window_color.lightness() < 128
        if is_dark:
            base_color = QColor(DARK_THEME.sidebar_background)
            hover_center = base_color.lighter(110)
            hover_outer = base_color
        else:
            base_color = palette.color(QPalette.ColorRole.Base)
            hover_center = base_color.lighter(105)
            hover_outer = base_color

        text_secondary = QColor(text_primary)
        text_secondary.setAlpha(160)

        self._base_color = base_color
        self._hover_center_color = hover_center
        self._hover_outer_color = hover_outer

        self.title_label.setStyleSheet(
            "color: "
            f"{text_primary.name(QColor.NameFormat.HexArgb)}; "
            "font-size: 14px; font-weight: 600; background: transparent;"
        )
        self.count_label.setStyleSheet(
            "color: "
            f"{text_secondary.name(QColor.NameFormat.HexArgb)}; "
            "font-size: 13px; background: transparent;"
        )

        placeholder_color = text_primary.name(QColor.NameFormat.HexArgb)
        self.image_view.setPlaceholder(
            load_icon("photo.on.rectangle", color=placeholder_color).pixmap(32, 32)
        )
        placeholder_bg = base_color.darker(110) if is_dark else QColor("#B0BEC5")
        self.image_view.set_background_color(placeholder_bg)
        self.update()

