"""Standalone widgets composed by :class:`AlbumSidebar`.

The drop-aware tree view, the hairline divider and the footer row are small,
self-contained widgets. Keeping them here leaves ``album_sidebar`` focused on
model wiring, selection and layout.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QTreeView,
    QWidget,
)

from ..icon import load_icon
from ..models.album_tree_model import AlbumTreeModel, NodeType
from ..palette import (
    SIDEBAR_ICON_SIZE,
    SIDEBAR_ICON_TEXT_GAP,
    SIDEBAR_LEFT_PADDING,
    SIDEBAR_ROW_HEIGHT,
    SIDEBAR_ROW_RADIUS,
    SIDEBAR_SELECTED_BACKGROUND,
)


class _DropAwareTree(QTreeView):
    """Tree view that accepts drops of external media files onto albums."""

    filesDropped = Signal(Path, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: AlbumTreeModel | None = None
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(True)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def setModel(self, model) -> None:
        super().setModel(model)
        if isinstance(model, AlbumTreeModel):
            self._model = model

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._should_accept_event(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._should_accept_event(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        target = self._resolve_target_path(event)
        paths = self._extract_local_files(event)
        if target is None or not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.filesDropped.emit(target, paths)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _should_accept_event(self, event: QDragEnterEvent | QDragMoveEvent) -> bool:
        if self._extract_local_files(event) == []:
            return False
        target = self._resolve_target_path(event)
        return target is not None

    def _resolve_target_path(
        self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent
    ) -> Path | None:
        model = self._model or self.model()
        if not isinstance(model, AlbumTreeModel):
            return None
        index = self.indexAt(self._event_pos(event))
        if not index.isValid():
            return None
        item = model.item_from_index(index)
        if item is None or item.album is None:
            return None
        if item.node_type not in {NodeType.ALBUM, NodeType.SUBALBUM}:
            return None
        return item.album.path

    def _extract_local_files(
        self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent
    ) -> list[Path]:
        mime = event.mimeData()
        if mime is None:
            return []
        urls = getattr(mime, "urls", None)
        if not callable(urls):
            return []
        seen: set[Path] = set()
        paths: list[Path] = []
        for url in urls():
            if not url.isLocalFile():
                continue
            local = Path(url.toLocalFile()).expanduser()
            if local in seen:
                continue
            seen.add(local)
            paths.append(local)
        return paths

    def _event_pos(self, event) -> QPoint:
        if hasattr(event, "position"):
            return event.position().toPoint()
        if hasattr(event, "pos"):
            return event.pos()
        return QPoint()


class _SidebarHairline(QWidget):
    """Hairline divider used above the sidebar footer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The extra height gives the rule breathing room without a layout spacer.
        self.setFixedHeight(13)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(24)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRect(0, 6, self.width(), 1)


class _SidebarFooterRow(QWidget):
    """Footer row that opens a menu, styled like a navigation entry.

    The reference sidebar keeps its global entry (settings) pinned below the
    navigation list, separated by a hairline. It uses the same row geometry as
    the navigation entries so the column reads as one control surface.
    """

    clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._hovered = False
        self.setFixedHeight(SIDEBAR_ROW_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SIDEBAR_LEFT_PADDING, 0, SIDEBAR_LEFT_PADDING, 0)
        layout.setSpacing(SIDEBAR_ICON_TEXT_GAP)

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self._icon_label)

        self._text_label = QLabel(text, self)
        self._text_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        text_font = QFont(self._text_label.font())
        text_font.setPixelSize(14)
        text_font.setWeight(QFont.Weight.Medium)
        self._text_label.setFont(text_font)
        layout.addWidget(self._text_label, 1)

        self._apply_colours()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._text_label.setText(text)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if self._hovered:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SIDEBAR_SELECTED_BACKGROUND))
            painter.drawRoundedRect(
                QRectF(self.rect()), SIDEBAR_ROW_RADIUS, SIDEBAR_ROW_RADIUS
            )
        super().paintEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._apply_colours()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._apply_colours()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # ``QWidget`` ignores presses by default, which would let the event
        # bubble to the parent and rob this row of the matching release.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_colours()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_colours(self) -> None:
        base = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour = QColor(base)
        if not self._hovered:
            colour.setAlpha(150)
        self._text_label.setStyleSheet(
            "color: rgba({}, {}, {}, {});".format(
                colour.red(), colour.green(), colour.blue(), colour.alpha()
            )
        )
        pixmap = load_icon(
            self._icon_name,
            color=colour.name(QColor.NameFormat.HexArgb),
            stroke_width=1.8,
        ).pixmap(SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE)
        self._icon_label.setPixmap(pixmap)

