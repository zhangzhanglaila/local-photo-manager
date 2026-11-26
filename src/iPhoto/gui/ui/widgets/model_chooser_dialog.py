"""Generic picker for the on-device AI models (faces, OCR, semantic search).

The settings page shows every model as a plain row with the active choice and a
"切换…" action, exactly like 语义搜索. This dialog is what those actions open: it
lists the packs with their size and trade-offs, and — unlike a combobox — it is
the *body* that scrolls when the list gets long, so short lists are shown in full
without any inner scrollbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon
from .dialog_theme import dialog_tokens

_MARGIN = 24


@dataclass(frozen=True)
class ModelOption:
    """One selectable pack."""

    key: str
    title: str
    summary: str
    details: str
    installed: bool = True


class ModelChooserDialog(QDialog):
    """Pick one model pack; returns the key through :meth:`selected_key`."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str,
        subtitle: str,
        options: Sequence[ModelOption],
        current_key: str | None = None,
        on_open_folder: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._on_open_folder = on_open_folder
        self._current_key = current_key or (self._options[0].key if self._options else "")
        self._selected_key = self._current_key

        self.setObjectName("modelChooserDialog")
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QWidget(self)
        shell.setObjectName("modelChooserShell")
        shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(shell)

        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(shell)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(_MARGIN, 20, 14, 0)
        header_layout.setSpacing(8)
        title_label = QLabel(title, header)
        title_label.setObjectName("modelChooserTitle")
        header_layout.addWidget(title_label, 1)
        close_button = QToolButton(header)
        close_button.setObjectName("modelChooserClose")
        close_button.setFixedSize(30, 30)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.reject)
        header_layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        body = QWidget(shell)
        body.setObjectName("modelChooserBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(_MARGIN, 8, _MARGIN, 0)
        body_layout.setSpacing(10)

        subtitle_label = QLabel(subtitle, body)
        subtitle_label.setObjectName("modelChooserSubtitle")
        subtitle_label.setWordWrap(True)
        body_layout.addWidget(subtitle_label)

        self._list = QListWidget(body)
        self._list.setObjectName("modelChooserList")
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        # No inner scrolling: the body owns the scrollbar when there is one.
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for option in self._options:
            label = option.title
            if not option.installed:
                label += tr("search.model.not_installed_suffix")
            item = QListWidgetItem(f"{label}  ·  {option.summary}")
            item.setData(Qt.ItemDataRole.UserRole, option.key)
            self._list.addItem(item)
            if option.key == self._current_key:
                self._list.setCurrentItem(item)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        body_layout.addWidget(self._list)

        self._details = QLabel("", body)
        self._details.setObjectName("modelChooserDetails")
        self._details.setWordWrap(True)
        body_layout.addWidget(self._details)
        body_layout.addSpacing(8)

        scroll = QScrollArea(shell)
        scroll.setObjectName("modelChooserScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self._scroll = scroll

        actions = QWidget(shell)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(_MARGIN, 14, _MARGIN, 20)
        actions_layout.setSpacing(8)
        self._folder_button = QPushButton(tr("search.model.open_folder"), actions)
        self._folder_button.setObjectName("modelChooserSecondary")
        self._folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_button.setMinimumHeight(32)
        self._folder_button.clicked.connect(self._open_folder)
        self._folder_button.setVisible(on_open_folder is not None)
        actions_layout.addWidget(self._folder_button)
        actions_layout.addStretch(1)

        cancel = QPushButton(tr("dialog.cancel"), actions)
        cancel.setObjectName("modelChooserSecondary")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setMinimumHeight(32)
        cancel.clicked.connect(self.reject)
        actions_layout.addWidget(cancel)

        self._use_button = QPushButton(tr("search.model.use"), actions)
        self._use_button.setObjectName("modelChooserPrimary")
        self._use_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_button.setMinimumHeight(32)
        self._use_button.clicked.connect(self._accept_selection)
        actions_layout.addWidget(self._use_button)
        layout.addWidget(actions)

        close_button.setIcon(
            load_icon(
                "window.close.svg",
                color=self._muted_icon_colour(),
                size=(12, 12),
            )
        )
        close_button.setIconSize(QSize(12, 12))

        self.setStyleSheet(self._stylesheet())
        self._refresh()
        self._size_to_content()

    # ------------------------------------------------------------------
    def selected_key(self) -> str:
        return self._selected_key

    def _option(self) -> ModelOption:
        item = self._list.currentItem()
        key = str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else self._current_key
        return next(
            (option for option in self._options if option.key == key),
            self._options[0],
        )

    def _on_selection_changed(self, _current, _previous) -> None:
        self._refresh()

    def _refresh(self) -> None:
        option = self._option()
        self._details.setText(option.details)
        self._use_button.setEnabled(option.installed)
        self._folder_button.setEnabled(option.installed)

    def _accept_selection(self) -> None:
        self._selected_key = self._option().key
        self.accept()

    def _open_folder(self) -> None:
        if self._on_open_folder is not None:
            self._on_open_folder(self._option().key)

    def _size_to_content(self) -> None:
        """Show everything; only a long list makes the body scroll."""

        self._list.setFixedHeight(self._list_height())
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            limit = int(screen.availableGeometry().height() * 0.8)
            if self.height() > limit:
                self.resize(self.width(), limit)

    def _list_height(self) -> int:
        rows = self._list.count()
        if rows <= 0:
            return 60
        last = self._list.item(rows - 1)
        rect = self._list.visualItemRect(last)
        if rect.height() > 0:
            return max(60, rect.top() + rect.height() + 10)
        return max(60, ((self._list.sizeHintForRow(0) or 30) + 16) * rows + 10)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._size_to_content()
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.window().frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _muted_icon_colour(self) -> str:
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(160)
        return colour.name(QColor.NameFormat.HexArgb)

    def _stylesheet(self) -> str:
        tokens = dialog_tokens(self)
        accent = tokens["accent"]
        on_accent = tokens["on_accent"]
        return (
            "QDialog#modelChooserDialog { background: transparent; }"
            f"QWidget#modelChooserShell {{ background-color: {tokens['shell']};"
            f"  border-radius: 14px; border: 1px solid {tokens['divider']}; }}"
            f"QLabel#modelChooserTitle {{ font-size: 16px; font-weight: 600; color: {tokens['text']}; }}"
            f"QLabel#modelChooserSubtitle {{ font-size: 12px; color: {tokens['muted']}; }}"
            f"QLabel#modelChooserDetails {{ font-size: 12px; color: {tokens['muted']}; }}"
            "QScrollArea#modelChooserScroll, QWidget#modelChooserBody { background: transparent; }"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {tokens['divider']}; border-radius: 5px;"
            "  min-height: 32px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            f"QListWidget#modelChooserList {{ border: 1px solid {tokens['divider']};"
            f"  border-radius: 10px; background-color: {tokens['field']}; padding: 4px;"
            f"  color: {tokens['text']}; font-size: 13px; outline: none; }}"
            f"QListWidget#modelChooserList::item {{ padding: 8px 6px; border-radius: 6px; }}"
            f"QListWidget#modelChooserList::item:selected {{ background-color: {accent};"
            f"  color: {on_accent}; }}"
            "QToolButton#modelChooserClose { border: none; border-radius: 8px; background: transparent; }"
            f"QToolButton#modelChooserClose:hover {{ background-color: {tokens['hover']}; }}"
            f"QPushButton#modelChooserSecondary {{ min-height: 32px; padding: 0 16px;"
            f"  border-radius: 8px; border: 1px solid {tokens['divider']};"
            f"  background-color: {tokens['secondary']}; color: {tokens['text']}; font-size: 12px; }}"
            f"QPushButton#modelChooserSecondary:hover {{ background-color: {tokens['hover']}; }}"
            f"QPushButton#modelChooserSecondary:disabled {{ color: {tokens['muted']}; }}"
            f"QPushButton#modelChooserPrimary {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: none; background-color: {accent};"
            f"  color: {on_accent}; font-size: 13px; font-weight: 600; }}"
            f"QPushButton#modelChooserPrimary:disabled {{ background-color: {tokens['secondary']};"
            f"  color: {tokens['muted']}; }}"
        )


__all__ = ["ModelChooserDialog", "ModelOption"]
