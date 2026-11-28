"""Browse the text that OCR found, grouped by photo.

The page is intentionally a separate view inside the settings stack: a back
arrow returns to 设置 → 智能, while the left list shows the photo thumbnail and
the right pane shows every recognized line for the selected photo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon
from ..tasks.thumbnail_loader import ThumbnailLoader
from .spinner import Spinner

#: Enough for a useful browse session without loading a huge OCR database into
#: the widget list. The count label makes the truncation explicit.
OCR_RESULTS_LIMIT = 500

#: Keep a Python wrapper alive while its QRunnable is still in the pool; the
#: page can be deleted (settings closed) before the OCR query returns.
_ACTIVE_WORKERS: set[QRunnable] = set()


class _OcrListSignals(QObject):
    loaded = Signal(object, int)
    failed = Signal(str)


class _OcrListWorker(QRunnable):
    """Read OCR assets and their text off the GUI thread."""

    def __init__(self, root: Path, signals: _OcrListSignals) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self.signals = signals

    def run(self) -> None:
        try:
            from ....ai.db.ocr_database import OCRRepository

            if not OCRRepository.db_path(self._root).exists():
                self.signals.loaded.emit([], 0)
                return
            repository = OCRRepository(self._root)
            try:
                assets = repository.list_assets_with_text(limit=OCR_RESULTS_LIMIT)
                total = repository.count_assets()
            finally:
                repository.close()
            self.signals.loaded.emit(assets, total)
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            self.signals.failed.emit(str(exc))
        finally:
            _ACTIVE_WORKERS.discard(self)


class OcrResultsPage(QWidget):
    """Photo list on the left, recognized text on the right."""

    backRequested = Signal()

    def __init__(self, root: Path | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ocrResultsPage")
        self._root = Path(root) if root is not None else None
        self._loaded = False
        self._closed = False
        self._worker = None
        self._thumbs: ThumbnailLoader | None = None
        self._items_by_rel: dict[str, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("ocrResultsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 22, 10)
        header_layout.setSpacing(10)

        self.back_button = QToolButton(header)
        self.back_button.setObjectName("settingsBackButton")
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(170)
        self.back_button.setIcon(
            load_icon(
                "chevron.left.svg",
                color=colour.name(QColor.NameFormat.HexArgb),
                size=(14, 14),
            )
        )
        self.back_button.setIconSize(QSize(14, 14))
        self.back_button.setFixedSize(32, 32)
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.setToolTip(tr("ocr.results.back"))
        self.back_button.clicked.connect(lambda: self.backRequested.emit())
        header_layout.addWidget(self.back_button)

        self.title_label = QLabel(tr("ocr.results.title"), header)
        self.title_label.setObjectName("settingsPageTitle")
        header_layout.addWidget(self.title_label, 1)

        self.spinner = Spinner(16, header)
        self.spinner.hide()
        header_layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.count_label = QLabel("", header)
        self.count_label.setObjectName("settingsPathLabel")
        header_layout.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(header)

        self._body = QWidget(self)
        self._body.setObjectName("ocrResultsBody")
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(22, 4, 22, 22)
        body_layout.setSpacing(0)

        self._message = QLabel("", self._body)
        self._message.setObjectName("settingsPathLabel")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_layout.addWidget(self._message, 1)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self._body)
        self._splitter.setObjectName("ocrResultsSplitter")
        self._splitter.setChildrenCollapsible(False)

        self._list = QListWidget(self._splitter)
        self._list.setObjectName("ocrResultsList")
        self._list.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list.setIconSize(QSize(82, 82))
        self._list.setSpacing(4)
        self._list.setUniformItemSizes(True)
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._splitter.addWidget(self._list)

        self._text = QPlainTextEdit(self._splitter)
        self._text.setObjectName("ocrResultsText")
        self._text.setReadOnly(True)
        self._text.setPlaceholderText(tr("ocr.results.text_placeholder"))
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._splitter.addWidget(self._text)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([300, 520])
        body_layout.addWidget(self._splitter, 1)

        self._splitter.hide()
        self._show_message(tr("ocr.results.loading"), loading=True)
        layout.addWidget(self._body, 1)

        self._list.setStyleSheet(
            "QListWidget#ocrResultsList { border: none; background: transparent; }"
            "QListWidget#ocrResultsList::item { padding: 5px 6px; border-radius: 8px; }"
            "QListWidget#ocrResultsList::item:selected { background: palette(highlight);"
            " color: palette(highlighted-text); }"
        )
        self._text.setStyleSheet(
            "QPlainTextEdit#ocrResultsText { border: 1px solid palette(mid);"
            " border-radius: 10px; padding: 10px; font-size: 15px;"
            " background: palette(base); color: palette(text); }"
        )

        if self._root is not None:
            self._thumbs = ThumbnailLoader(self, library_root=self._root)
            self._thumbs.reset_for_album(self._root)
            self._thumbs.ready.connect(self._on_thumbnail_ready)

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Load the OCR list once; repeated visits keep the already loaded rows."""

        if self._loaded or self._closed:
            return
        if self._root is None:
            self._show_message(tr("ocr.results.no_library"))
            self._loaded = True
            return
        self._loaded = True
        self._show_message(tr("ocr.results.loading"), loading=True)
        signals = _OcrListSignals()
        worker = _OcrListWorker(self._root, signals)
        signals.loaded.connect(self._on_assets_loaded)
        signals.failed.connect(self._on_load_failed)
        self._worker = worker
        _ACTIVE_WORKERS.add(worker)
        QThreadPool.globalInstance().start(worker)

    def shutdown(self) -> None:
        self._closed = True
        if self._thumbs is not None:
            self._thumbs.blockSignals(True)

    # ------------------------------------------------------------------
    def _show_message(self, message: str, *, loading: bool = False) -> None:
        self._message.setText(message)
        self._message.show()
        if loading:
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()

    def _on_assets_loaded(self, assets, total: int) -> None:
        self._worker = None
        if self._closed:
            return
        self._items_by_rel.clear()
        self._list.clear()
        if not assets:
            self._splitter.hide()
            self._show_message(tr("ocr.results.empty"))
            self.count_label.setText("")
            return

        self._message.hide()
        self.spinner.stop()
        self.spinner.hide()
        self._splitter.show()
        placeholder = load_icon("photo.on.rectangle.svg", size=(64, 64))
        for asset in assets:
            # OCR rows may store an absolute path for an extra library root;
            # the list only needs the photo name, with the full path in the tip.
            name = Path(asset.asset_rel).name or asset.asset_rel
            item = QListWidgetItem(placeholder, name, self._list)
            item.setData(Qt.ItemDataRole.UserRole, asset.text)
            item.setToolTip(asset.asset_rel)
            self._items_by_rel[asset.asset_rel] = item
            self._request_thumbnail(asset.asset_rel)
        self.count_label.setText(
            tr("ocr.results.count", shown=len(assets), total=max(total, len(assets)))
        )
        self._list.setCurrentRow(0)

    def _on_load_failed(self, message: str) -> None:
        self._worker = None
        if self._closed:
            return
        self._splitter.hide()
        self._show_message(tr("ocr.results.failed", message=message))

    def _on_current_item_changed(self, current, _previous) -> None:
        if current is None:
            self._text.clear()
            return
        text = current.data(Qt.ItemDataRole.UserRole) or ""
        self._text.setPlainText(text or tr("ocr.results.no_text"))

    def _request_thumbnail(self, rel: str) -> None:
        if self._thumbs is None or self._root is None:
            return
        source = self._root / rel
        if not source.exists():
            return
        pixmap = self._thumbs.request(
            rel,
            source,
            QSize(82, 82),
            is_image=True,
        )
        if pixmap is not None:
            self._set_thumbnail(rel, pixmap)

    def _on_thumbnail_ready(self, _root: Path, rel: str, pixmap) -> None:
        if self._closed:
            return
        self._set_thumbnail(rel, pixmap)

    def _set_thumbnail(self, rel: str, pixmap) -> None:
        item = self._items_by_rel.get(rel)
        if item is not None and not pixmap.isNull():
            item.setIcon(QIcon(pixmap))


__all__ = ["OcrResultsPage"]
