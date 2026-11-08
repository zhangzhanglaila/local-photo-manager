"""Dropdown panel shown below the header search field.

Holds the pieces that make the search box quick to use without doing any work while
it is closed: recent queries, the search scope and a few thumbnails that start a
"find similar" search. It is a plain child widget of the main window (not a focus
grabbing popup) so typing in the search field continues uninterrupted.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr

_HISTORY_COLUMNS = 3
_SIMILAR_THUMB_SIZE = 72


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
            "  border-radius: 10px;"
            "}"
            "QFrame#searchPopup QLabel { border: none; background: transparent; }"
            "QFrame#searchPopup QPushButton {"
            "  border: 1px solid palette(mid);"
            "  border-radius: 12px;"
            "  padding: 3px 10px;"
            "  background: palette(button);"
            "}"
            "QFrame#searchPopup QPushButton:hover { background: palette(midlight); }"
            "QFrame#searchPopup QPushButton:checked {"
            "  background: palette(highlight);"
            "  color: palette(highlighted-text);"
            "  border-color: palette(highlight);"
            "}"
        )
        self._scope = "all"
        self._album_label: str | None = None
        self._similar: dict[str, QPixmap] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # --- Scope -----------------------------------------------------
        scope_row = QHBoxLayout()
        scope_row.setSpacing(6)
        self._scope_label = QLabel(tr("search.popup.scope"))
        scope_row.addWidget(self._scope_label)
        self._scope_all = QPushButton(tr("search.popup.scope_all"))
        self._scope_all.setCheckable(True)
        self._scope_all.setChecked(True)
        self._scope_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scope_all.clicked.connect(lambda: self._set_scope("all"))
        scope_row.addWidget(self._scope_all)
        self._scope_album = QPushButton(tr("search.popup.scope_album"))
        self._scope_album.setCheckable(True)
        self._scope_album.setEnabled(False)
        self._scope_album.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scope_album.clicked.connect(lambda: self._set_scope("album"))
        scope_row.addWidget(self._scope_album)
        scope_row.addStretch(1)
        outer.addLayout(scope_row)

        # --- History ---------------------------------------------------
        history_row = QHBoxLayout()
        history_row.setSpacing(6)
        self._history_label = QLabel(tr("search.popup.history"))
        history_row.addWidget(self._history_label)
        history_row.addStretch(1)
        self._clear_history = QPushButton(tr("search.popup.clear"))
        self._clear_history.setFlat(True)
        self._clear_history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_history.setStyleSheet(
            "QPushButton { border: none; background: transparent; padding: 0; }"
            "QPushButton:hover { color: palette(highlight); }"
        )
        self._clear_history.clicked.connect(self.clear_history_requested.emit)
        history_row.addWidget(self._clear_history)
        outer.addLayout(history_row)

        self._history_container = QWidget()
        self._history_grid = QGridLayout(self._history_container)
        self._history_grid.setContentsMargins(0, 0, 0, 0)
        self._history_grid.setSpacing(6)
        outer.addWidget(self._history_container)

        # --- Similar photos -------------------------------------------
        self._similar_header = QLabel(tr("search.popup.similar"))
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

    def retranslate(self) -> None:
        """Refresh labels after a language switch."""

        self._scope_label.setText(tr("search.popup.scope"))
        self._scope_all.setText(tr("search.popup.scope_all"))
        self._scope_album.setText(tr("search.popup.scope_album"))
        self._history_label.setText(tr("search.popup.history"))
        self._clear_history.setText(tr("search.popup.clear"))
        self._similar_header.setText(tr("search.popup.similar"))

    def set_history(self, queries: Sequence[str]) -> None:
        while self._history_grid.count():
            item = self._history_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not queries:
            empty = QLabel(tr("search.popup.history_empty"))
            empty.setStyleSheet("color: palette(mid);")
            self._history_grid.addWidget(empty, 0, 0, 1, _HISTORY_COLUMNS)
            return

        for index, query in enumerate(queries[:_HISTORY_COLUMNS * 3]):
            label = query if len(query) <= 18 else query[:17] + "…"
            chip = QPushButton(label)
            chip.setToolTip(query)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.clicked.connect(
                lambda _checked=False, q=query: self.query_selected.emit(q)
            )
            self._history_grid.addWidget(
                chip, index // _HISTORY_COLUMNS, index % _HISTORY_COLUMNS
            )

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
        # Always re-check: a checkable button toggles itself off when clicked
        # while already active.
        self._scope_all.setChecked(scope == "all")
        self._scope_album.setChecked(scope == "album")
        if changed:
            self.scope_changed.emit(scope)

    def _refresh_similar(self) -> None:
        while self._similar_row.count():
            item = self._similar_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        visible = list(self._similar.items())[:8]
        self._similar_header.setVisible(bool(visible))
        self._similar_container.setVisible(bool(visible))
        for asset_id, pixmap in visible:
            button = QPushButton()
            button.setFixedSize(_SIMILAR_THUMB_SIZE, _SIMILAR_THUMB_SIZE)
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(_SIMILAR_THUMB_SIZE - 8, _SIMILAR_THUMB_SIZE - 8))
            button.setToolTip(tr("search.popup.similar_tooltip"))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, aid=asset_id: self.similar_selected.emit(aid)
            )
            self._similar_row.addWidget(button)
        self._similar_row.addStretch(1)
        self.adjustSize()
