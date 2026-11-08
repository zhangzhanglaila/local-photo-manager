"""Dropdown panel shown below the header search field.

Visually modelled on the search panels of cloud-drive apps: a floating white card
with a soft shadow, a scope switch, wrapping history chips and a strip of suggested
thumbnails. It does no work while closed, and it is a plain child widget of the main
window (not a focus-grabbing popup) so typing continues uninterrupted.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon

_SIMILAR_THUMB_SIZE = 44
_MAX_HISTORY = 9
_MAX_SIMILAR = 8


class _FlowLayout(QLayout):
    """Left-to-right layout that wraps at the available width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class SearchPopup(QFrame):
    """Card with scope, recent searches and similar-photo shortcuts."""

    query_selected = Signal(str)
    similar_selected = Signal(str)
    clear_history_requested = Signal()
    scope_changed = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("searchPopup")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            "QFrame#searchPopup {"
            "  background: palette(base);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 12px;"
            "}"
            "QFrame#searchPopup QLabel { border: none; background: transparent; }"
            "QLabel#popupSection {"
            "  color: palette(placeholder-text);"
            "  font-size: 12px;"
            "}"
            "QPushButton#scopeButton {"
            "  border: 1px solid transparent;"
            "  background: transparent;"
            "  border-radius: 8px;"
            "  padding: 4px 10px;"
            "  color: palette(text);"
            "  font-size: 13px;"
            "}"
            "QPushButton#scopeButton:hover { background: palette(midlight); }"
            "QPushButton#scopeButton:checked {"
            "  background: palette(base);"
            "  border: 1px solid palette(mid);"
            "}"
            "QPushButton#historyChip {"
            "  background: palette(midlight);"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 6px 10px;"
            "  color: palette(text);"
            "  font-size: 13px;"
            "}"
            "QPushButton#historyChip:hover { background: palette(mid); }"
            "QPushButton#popupLink {"
            "  border: none;"
            "  background: transparent;"
            "  color: palette(placeholder-text);"
            "  font-size: 12px;"
            "  padding: 0;"
            "}"
            "QPushButton#popupLink:hover { color: palette(highlight); }"
            "QPushButton#similarThumb {"
            "  border: none;"
            "  background: transparent;"
            "  padding: 0;"
            "}"
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

        self._scope = "all"
        self._album_label: str | None = None
        self._similar: dict[str, QPixmap] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        # --- Scope -----------------------------------------------------
        scope_row = QHBoxLayout()
        scope_row.setSpacing(8)
        self._scope_label = QLabel(tr("search.popup.scope"))
        self._scope_label.setObjectName("popupSection")
        scope_row.addWidget(self._scope_label)

        self._scope_all = QPushButton(tr("search.popup.scope_all"))
        self._scope_all.setObjectName("scopeButton")
        self._scope_all.setCheckable(True)
        self._scope_all.setChecked(True)
        self._scope_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scope_all.setIcon(load_icon("photo.on.rectangle.svg", size=(16, 16)))
        self._scope_all.setIconSize(QSize(16, 16))
        self._scope_all.clicked.connect(lambda: self._set_scope("all"))
        scope_row.addWidget(self._scope_all)

        self._scope_album = QPushButton(tr("search.popup.scope_album"))
        self._scope_album.setObjectName("scopeButton")
        self._scope_album.setCheckable(True)
        self._scope_album.setEnabled(False)
        self._scope_album.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scope_album.setIcon(load_icon("folder.svg", size=(16, 16)))
        self._scope_album.setIconSize(QSize(16, 16))
        self._scope_album.clicked.connect(lambda: self._set_scope("album"))
        scope_row.addWidget(self._scope_album)
        scope_row.addStretch(1)
        outer.addLayout(scope_row)

        # --- History ---------------------------------------------------
        history_row = QHBoxLayout()
        history_row.setSpacing(8)
        self._history_label = QLabel(tr("search.popup.history"))
        self._history_label.setObjectName("popupSection")
        history_row.addWidget(self._history_label)
        history_row.addStretch(1)
        self._clear_history = QPushButton(tr("search.popup.clear"))
        self._clear_history.setObjectName("popupLink")
        self._clear_history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_history.setIcon(load_icon("trash.svg", size=(13, 13)))
        self._clear_history.setIconSize(QSize(13, 13))
        self._clear_history.clicked.connect(self.clear_history_requested.emit)
        history_row.addWidget(self._clear_history)
        outer.addLayout(history_row)

        self._history_container = QWidget()
        self._history_layout = _FlowLayout(self._history_container, spacing=8)
        outer.addWidget(self._history_container)

        # --- Similar photos -------------------------------------------
        self._similar_header = QLabel(tr("search.popup.similar"))
        self._similar_header.setObjectName("popupSection")
        outer.addWidget(self._similar_header)

        self._similar_container = QWidget()
        self._similar_row = QHBoxLayout(self._similar_container)
        self._similar_row.setContentsMargins(0, 0, 0, 0)
        self._similar_row.setSpacing(6)
        outer.addWidget(self._similar_container)

        self.set_history([])
        self._refresh_similar()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def current_scope(self) -> str:
        return self._scope

    def refresh_size(self) -> None:
        """Grow to the height the current content needs at the current width."""

        width = self.width()
        if width <= 0:
            return
        height = self.heightForWidth(width)
        if height > 0:
            self.setFixedHeight(height)

    def retranslate(self) -> None:
        """Refresh labels after a language switch."""

        self._scope_label.setText(tr("search.popup.scope"))
        self._scope_all.setText(tr("search.popup.scope_all"))
        self._scope_album.setText(tr("search.popup.scope_album"))
        self._history_label.setText(tr("search.popup.history"))
        self._clear_history.setText(tr("search.popup.clear"))
        self._similar_header.setText(tr("search.popup.similar"))

    def set_history(self, queries: Sequence[str]) -> None:
        while self._history_layout.count():
            item = self._history_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        if not queries:
            empty = QLabel(tr("search.popup.history_empty"))
            empty.setObjectName("popupSection")
            self._history_layout.addWidget(empty)
            self.refresh_size()
            return

        for query in queries[:_MAX_HISTORY]:
            label = query if len(query) <= 18 else query[:17] + "…"
            chip = QPushButton(label)
            chip.setObjectName("historyChip")
            chip.setToolTip(query)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.clicked.connect(
                lambda _checked=False, q=query: self.query_selected.emit(q)
            )
            self._history_layout.addWidget(chip)
        self.refresh_size()

    def set_album_scope(self, label: str | None) -> None:
        self._album_label = label
        self._scope_album.setEnabled(bool(label))
        self._scope_album.setToolTip(label or "")
        if not label and self._scope == "album":
            self._set_scope("all")

    def set_similar_photos(self, items: Iterable[tuple[str, QPixmap]]) -> None:
        self._similar.clear()
        for asset_id, pixmap in items:
            self._similar[str(asset_id)] = pixmap
        self._refresh_similar()

    def add_similar_photo(self, asset_id: str, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self._similar[str(asset_id)] = pixmap
        self._refresh_similar()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _set_scope(self, scope: str) -> None:
        if scope == "album" and not self._album_label:
            scope = "all"
        changed = scope != self._scope
        self._scope = scope
        # A checkable button toggles itself off when clicked while active; keep the
        # active scope checked regardless.
        self._scope_all.setChecked(scope == "all")
        self._scope_album.setChecked(scope == "album")
        if changed:
            self.scope_changed.emit(scope)

    def _refresh_similar(self) -> None:
        while self._similar_row.count():
            item = self._similar_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        visible = list(self._similar.items())[:_MAX_SIMILAR]
        self._similar_header.setVisible(bool(visible))
        self._similar_container.setVisible(bool(visible))
        for asset_id, pixmap in visible:
            button = QPushButton()
            button.setObjectName("similarThumb")
            button.setFixedSize(_SIMILAR_THUMB_SIZE, _SIMILAR_THUMB_SIZE)
            button.setIcon(QIcon(_rounded_thumbnail(pixmap)))
            button.setIconSize(QSize(_SIMILAR_THUMB_SIZE, _SIMILAR_THUMB_SIZE))
            button.setToolTip(tr("search.popup.similar_tooltip"))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, aid=asset_id: self.similar_selected.emit(aid)
            )
            self._similar_row.addWidget(button)
        self._similar_row.addStretch(1)
        self.refresh_size()


def _rounded_thumbnail(pixmap: QPixmap, radius: int = 6) -> QPixmap:
    """Return a square, centre-cropped and rounded copy of *pixmap*."""

    size = QSize(_SIMILAR_THUMB_SIZE, _SIMILAR_THUMB_SIZE)
    scaled = pixmap.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    rounded = QPixmap(size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(path)
    offset_x = (size.width() - scaled.width()) // 2
    offset_y = (size.height() - scaled.height()) // 2
    painter.drawPixmap(offset_x, offset_y, scaled)
    painter.end()
    return rounded
