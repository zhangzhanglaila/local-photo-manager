"""Browse the text that OCR found, grouped by photo.

The page is intentionally a separate view inside the settings stack: a back
arrow returns to 设置 → 智能, while the left list shows the photo thumbnail and
the right pane shows every recognized line for the selected photo.

The photo list is virtualised: a ``QListView`` paints only the visible rows and
the model fetches 200-photo pages as the user scrolls, so a library with tens of
thousands of OCR results stays browsable without building one widget (and one
thumbnail) per photo.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPlainTextEdit,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ....utils.image_loader import load_qimage
from ..icon import load_icon
from ..tasks.thumbnail_loader import ThumbnailLoader
from .image_viewer import ImageViewer
from .spinner import Spinner

#: Photos fetched per page. The view asks for the next page when the scroll
#: bar approaches the end, so only this many rows are ever materialised at once.
OCR_RESULTS_PAGE_SIZE = 200

#: Thumbnails are requested for visible rows only and cached under a small
#: bound; the list must not pin tens of thousands of 82×82 pixmaps.
OCR_THUMBNAIL_CACHE_SIZE = 300

#: Preview images are decoded down to this box. It is large enough for the
#: inline viewer at 4× zoom without paying for a full RAW decode on every click.
OCR_PREVIEW_MAX_SIZE = QSize(1600, 1600)

#: Keep only the recently viewed previews; one QPixmap per entry is ~10 MB at
#: the size above, so an unbounded cache would grow while browsing.
OCR_PREVIEW_CACHE_SIZE = 4

#: Selection settles before the preview decode starts. Rapid arrow-key browsing
#: therefore decodes only the row the user stops on.
_PREVIEW_DEBOUNCE_MS = 150

#: One preview decode at a time: a slower old decode cannot queue a burst of
#: newer ones behind it. The newest request replaces the pending one.
_PREVIEW_POOL = QThreadPool()
_PREVIEW_POOL.setMaxThreadCount(1)
_PREVIEW_WORKERS: set[QRunnable] = set()

#: Rows before the scrollbar reaches the exact bottom start loading, so the next
#: page is usually ready by the time the user gets there.
_PREFETCH_DISTANCE = 400
_THUMBNAIL_SIZE = QSize(82, 82)
_FALLBACK_VISIBLE_ROWS = 12

#: Keep a Python wrapper alive while its QRunnable is still in the pool; the
#: page can be deleted (settings closed) before the OCR query returns.
_ACTIVE_WORKERS: set[QRunnable] = set()


class OcrAssetListModel(QAbstractListModel):
    """Paged list model over the photos that have OCR text."""

    fetchRequested = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._assets: list = []
        self._total = 0
        self._total_known = False
        self._loading = False
        self._rows_by_rel: dict[str, int] = {}
        self._placeholder = load_icon("photo.on.rectangle.svg", size=(64, 64))
        self._icons: dict[str, QIcon] = {}
        self._icon_order: list[str] = []

    # ------------------------------------------------------------------
    # Qt model API
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._assets)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or not 0 <= index.row() < len(self._assets):
            return None
        asset = self._assets[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            name = Path(asset.asset_rel).name
            return name or asset.asset_rel
        if role == Qt.ItemDataRole.ToolTipRole:
            return asset.asset_rel
        if role == Qt.ItemDataRole.DecorationRole:
            return self._icons.get(asset.asset_rel, self._placeholder)
        if role == Qt.ItemDataRole.UserRole:
            return asset.text
        return None

    def flags(self, index: QModelIndex):  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: N802
        if parent.isValid() or self._loading:
            return False
        if not self._total_known:
            # Before the first page the total is unknown; allow that first fetch.
            return not self._assets
        return len(self._assets) < self._total

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:  # noqa: N802
        if not self.canFetchMore(parent):
            return
        self._loading = True
        self.fetchRequested.emit(len(self._assets))

    # ------------------------------------------------------------------
    # Page management
    # ------------------------------------------------------------------
    def add_page(self, assets, total: int | None = None) -> None:
        """Append one page and adopt *total* when the caller knows it."""

        self._loading = False
        if total is not None:
            self._total = max(int(total), len(self._assets) + len(assets))
            self._total_known = True
        if not assets:
            # An empty page means the end of the table, even for a stale total.
            self._total = len(self._assets)
            self._total_known = True
            return
        start = len(self._assets)
        self.beginInsertRows(QModelIndex(), start, start + len(assets) - 1)
        for asset in assets:
            self._rows_by_rel[asset.asset_rel] = len(self._assets)
            self._assets.append(asset)
        self.endInsertRows()

    def load_failed(self) -> None:
        self._loading = False

    def clear_assets(self) -> None:
        self.beginResetModel()
        self._assets.clear()
        self._rows_by_rel.clear()
        self._icons.clear()
        self._icon_order.clear()
        self._total = 0
        self._total_known = False
        self._loading = False
        self.endResetModel()

    # ------------------------------------------------------------------
    # Accessors used by the page
    # ------------------------------------------------------------------
    def asset_at(self, row: int):
        if 0 <= row < len(self._assets):
            return self._assets[row]
        return None

    def total(self) -> int:
        return self._total

    def loaded_count(self) -> int:
        return len(self._assets)

    def is_loading(self) -> bool:
        return self._loading

    def has_thumbnail(self, rel: str) -> bool:
        return rel in self._icons

    def set_thumbnail(self, rel: str, pixmap) -> None:
        row = self._rows_by_rel.get(rel)
        if row is None or pixmap is None or pixmap.isNull():
            return
        self._icons[rel] = QIcon(pixmap)
        self._icon_order.append(rel)
        while len(self._icon_order) > OCR_THUMBNAIL_CACHE_SIZE:
            stale = self._icon_order.pop(0)
            if stale != rel:
                self._icons.pop(stale, None)
        index = self.index(row, 0)
        self.dataChanged.emit(
            index, index, [Qt.ItemDataRole.DecorationRole]
        )


class _OcrListSignals(QObject):
    loaded = Signal(object, object, int, int)
    failed = Signal(str, int)


class _OcrListWorker(QRunnable):
    """Read one page of OCR assets off the GUI thread."""

    def __init__(
        self,
        root: Path,
        signals: _OcrListSignals,
        offset: int,
        limit: int,
        generation: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self.signals = signals
        self._offset = offset
        self._limit = limit
        self._generation = generation

    def run(self) -> None:
        try:
            from ....ai.db.ocr_database import OCRRepository

            if not OCRRepository.db_path(self._root).exists():
                self.signals.loaded.emit([], 0, self._offset, self._generation)
                return
            repository = OCRRepository(self._root)
            try:
                assets = repository.list_assets_with_text(
                    limit=self._limit,
                    offset=self._offset,
                )
                # The total is only needed for the first page; later pages keep
                # the value the model already holds.
                total = repository.count_assets() if self._offset == 0 else None
            finally:
                repository.close()
            self.signals.loaded.emit(assets, total, self._offset, self._generation)
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            self.signals.failed.emit(str(exc), self._generation)
        finally:
            _ACTIVE_WORKERS.discard(self)


class _PreviewSignals(QObject):
    ready = Signal(str, object, int)
    failed = Signal(str, int)


class _PreviewWorker(QRunnable):
    """Decode one inline preview away from the GUI thread."""

    def __init__(
        self,
        rel: str,
        source: Path,
        token: int,
        signals: _PreviewSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._rel = rel
        self._source = source
        self._token = token
        self.signals = signals

    def run(self) -> None:
        try:
            image = load_qimage(self._source, OCR_PREVIEW_MAX_SIZE)
            if image is None or image.isNull():
                self.signals.failed.emit(self._rel, self._token)
                return
            self.signals.ready.emit(self._rel, image, self._token)
        except Exception:  # noqa: BLE001 - a broken preview must not kill the page
            self.signals.failed.emit(self._rel, self._token)
        finally:
            _PREVIEW_WORKERS.discard(self)


class OcrResultsPage(QWidget):
    """Photo list on the left, recognized text on the right."""

    backRequested = Signal()

    def __init__(self, root: Path | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ocrResultsPage")
        self._root = Path(root) if root is not None else None
        self._closed = False
        self._generation = 0
        self._worker = None
        self._thumbs: ThumbnailLoader | None = None
        self._pending_thumbnails: set[str] = set()
        self._preview_token = 0
        self._preview_rel = ""
        self._preview_busy = False
        self._preview_pending: tuple[str, Path, int] | None = None
        self._preview_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._start_pending_preview)

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

        self._model = OcrAssetListModel(self)
        self._list = QListView(self._splitter)
        self._list.setObjectName("ocrResultsList")
        self._list.setModel(self._model)
        self._list.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list.setIconSize(_THUMBNAIL_SIZE)
        self._list.setSpacing(4)
        self._list.setUniformItemSizes(True)
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self._list.selectionModel().currentChanged.connect(
            self._on_current_item_changed
        )
        self._list.viewport().installEventFilter(self)
        self._model.fetchRequested.connect(self._load_page)
        self._splitter.addWidget(self._list)

        self._text = QPlainTextEdit(self._splitter)
        self._text.setObjectName("ocrResultsText")
        self._text.setReadOnly(True)
        self._text.setPlaceholderText(tr("ocr.results.text_placeholder"))
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        self._preview = ImageViewer(self._splitter)
        self._preview.setObjectName("ocrResultsPreview")
        self._preview.set_wheel_action("zoom")
        self._preview.set_loading_text(tr("ocr.results.preview_loading"))
        self._preview.setMinimumHeight(180)
        self._preview.fullscreenExitRequested.connect(
            lambda: self._preview.set_zoom(1.0)
        )

        # The right-hand pane stacks the large preview above the recognized
        # text; the divider lets the user trade image space for text.
        self._detail_splitter = QSplitter(
            Qt.Orientation.Vertical, self._splitter
        )
        self._detail_splitter.setObjectName("ocrResultsDetailSplitter")
        self._detail_splitter.setChildrenCollapsible(False)
        self._detail_splitter.addWidget(self._preview)
        self._detail_splitter.addWidget(self._text)
        self._detail_splitter.setStretchFactor(0, 3)
        self._detail_splitter.setStretchFactor(1, 2)
        self._detail_splitter.setSizes([360, 220])
        self._splitter.addWidget(self._detail_splitter)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([300, 520])
        body_layout.addWidget(self._splitter, 1)

        self._splitter.hide()
        self._show_message(tr("ocr.results.loading"), loading=True)
        layout.addWidget(self._body, 1)

        self._list.setStyleSheet(
            "QListView#ocrResultsList { border: none; background: transparent; }"
            "QListView#ocrResultsList::item { padding: 5px 6px;"
            " border-radius: 8px; }"
            "QListView#ocrResultsList::item:selected {"
            " background: palette(highlight);"
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
    # Lifecycle
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Reload from the first page; called every time the page opens.

        Re-reading the first page (instead of caching the list forever) keeps
        the browser in sync after an OCR rescan without restarting the app.
        """

        if self._closed:
            return
        self._generation += 1
        self._worker = None
        self._pending_thumbnails.clear()
        self._preview_token += 1
        self._preview_timer.stop()
        self._preview_pending = None
        self._preview_cache.clear()
        self._clear_preview()
        self._model.clear_assets()
        self._splitter.hide()
        self._text.clear()
        self.count_label.setText("")
        if self._root is None:
            self._show_message(tr("ocr.results.no_library"))
            return
        self._show_message(tr("ocr.results.loading"), loading=True)
        self._model.fetchMore(QModelIndex())

    def shutdown(self) -> None:
        self._closed = True
        self._generation += 1
        self._preview_token += 1
        self._preview_timer.stop()
        self._preview_pending = None
        if self._thumbs is not None:
            self._thumbs.blockSignals(True)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._list.viewport() and event.type() == QEvent.Type.Resize:
            self._request_visible_thumbnails()
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load_page(self, offset: int) -> None:
        if self._closed or self._root is None:
            self._model.load_failed()
            return
        if self._worker is not None:
            self._model.load_failed()
            return
        generation = self._generation
        signals = _OcrListSignals()
        worker = _OcrListWorker(
            self._root,
            signals,
            offset,
            OCR_RESULTS_PAGE_SIZE,
            generation,
        )
        signals.loaded.connect(self._on_assets_loaded)
        signals.failed.connect(self._on_load_failed)
        self._worker = worker
        _ACTIVE_WORKERS.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_assets_loaded(self, assets, total, offset: int, generation: int) -> None:
        if self._closed or generation != self._generation:
            return
        self._worker = None
        self._model.add_page(assets, total)
        if offset == 0 and not assets:
            self._splitter.hide()
            self._show_message(tr("ocr.results.empty"))
            self.count_label.setText("")
            return
        if offset == 0:
            self._message.hide()
            self.spinner.stop()
            self.spinner.hide()
            self._splitter.show()
        total_count = max(self._model.total(), self._model.loaded_count())
        self.count_label.setText(
            tr(
                "ocr.results.count",
                shown=self._model.loaded_count(),
                total=total_count,
            )
        )
        if offset == 0 and self._model.loaded_count():
            self._list.setCurrentIndex(self._model.index(0, 0))
        QTimer.singleShot(0, self._after_page_loaded)

    def _on_load_failed(self, message: str, generation: int) -> None:
        if self._closed or generation != self._generation:
            return
        self._worker = None
        self._model.load_failed()
        if self._model.loaded_count() == 0:
            self._splitter.hide()
            self._show_message(tr("ocr.results.failed", message=message))
            self.count_label.setText("")

    def _after_page_loaded(self) -> None:
        if self._closed:
            return
        self._request_visible_thumbnails()
        self._maybe_fetch_more()

    # ------------------------------------------------------------------
    # Scrolling and thumbnails
    # ------------------------------------------------------------------
    def _on_scrolled(self, _value: int) -> None:
        self._request_visible_thumbnails()
        self._maybe_fetch_more()

    def _maybe_fetch_more(self) -> None:
        if self._closed:
            return
        bar = self._list.verticalScrollBar()
        # ``0`` means either the rows fit the viewport or the view has not been
        # laid out yet; in both cases there is nothing to prefetch.
        if bar.maximum() <= 0:
            return
        if bar.maximum() - bar.value() <= _PREFETCH_DISTANCE:
            self._model.fetchMore(QModelIndex())

    def _request_visible_thumbnails(self) -> None:
        if self._thumbs is None or self._root is None or self._closed:
            return
        viewport = self._list.viewport()
        # ``indexAt`` needs a point inside an item; (0, 0) falls in the view's
        # spacing gutter, which used to make every lookup return -1.
        x = max(1, viewport.width() // 2)
        margin = max(1, self._list.spacing())
        top = self._list.indexAt(QPoint(x, margin + 1))
        if top.isValid():
            bottom = self._list.indexAt(
                QPoint(x, max(0, viewport.height() - margin - 1))
            )
            first = max(0, top.row() - 2)
            if bottom.isValid():
                last = bottom.row()
            else:
                # The viewport is shorter than one row; do not fall back to
                # requesting every loaded thumbnail.
                last = top.row()
            last = min(self._model.loaded_count() - 1, last + 2)
        else:
            # The page has not been laid out yet (or is hidden); prefetch the
            # rows a first screen would show.
            first = 0
            last = min(
                self._model.loaded_count() - 1, _FALLBACK_VISIBLE_ROWS - 1
            )
        for row in range(first, last + 1):
            asset = self._model.asset_at(row)
            if asset is None:
                continue
            rel = asset.asset_rel
            if self._model.has_thumbnail(rel) or rel in self._pending_thumbnails:
                continue
            source = self._root / rel
            if not source.exists():
                continue
            self._pending_thumbnails.add(rel)
            pixmap = self._thumbs.request(
                rel,
                source,
                _THUMBNAIL_SIZE,
                is_image=True,
            )
            if pixmap is not None:
                self._pending_thumbnails.discard(rel)
                self._model.set_thumbnail(rel, pixmap)

    def _on_thumbnail_ready(self, _root: Path, rel: str, pixmap) -> None:
        if self._closed:
            return
        self._pending_thumbnails.discard(rel)
        if not pixmap.isNull():
            self._model.set_thumbnail(rel, pixmap)

    # ------------------------------------------------------------------
    # Selection / messages
    # ------------------------------------------------------------------
    def _on_current_item_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        self._preview_token += 1
        self._preview_timer.stop()
        self._preview_pending = None
        if not current.isValid():
            self._text.clear()
            self._clear_preview()
            return
        asset = self._model.asset_at(current.row())
        if asset is None:
            self._text.clear()
            self._clear_preview()
            return
        self._text.setPlainText(asset.text or tr("ocr.results.no_text"))
        self._schedule_preview(asset)

    # ------------------------------------------------------------------
    # Inline preview
    # ------------------------------------------------------------------
    def _schedule_preview(self, asset) -> None:
        """Show a cached preview immediately, otherwise debounce a decode."""

        rel = asset.asset_rel
        self._preview_rel = rel
        cached = self._preview_cache.get(rel)
        if cached is not None:
            self._preview_cache.move_to_end(rel)
            self._preview.set_loading(False)
            self._preview.set_pixmap(cached)
            return

        source = Path(rel)
        if not source.is_absolute() and self._root is not None:
            source = self._root / source
        self._preview.set_loading(True)
        self._preview_pending = (rel, source, self._preview_token)
        self._preview_timer.start()

    def _start_pending_preview(self) -> None:
        pending = self._preview_pending
        self._preview_pending = None
        if pending is None:
            return
        rel, source, token = pending
        if token != self._preview_token or rel != self._preview_rel:
            return
        self._request_preview(rel, source, token)

    def _request_preview(self, rel: str, source: Path, token: int) -> None:
        if self._closed:
            return
        if self._preview_busy:
            # The current decode cannot be cancelled; remember the newest row
            # and start it as soon as the current worker finishes.
            self._preview_pending = (rel, source, token)
            return
        if not source.exists():
            self._preview.set_loading(False)
            self._preview.set_pixmap(None)
            return
        self._preview_busy = True
        signals = _PreviewSignals()
        worker = _PreviewWorker(rel, source, token, signals)
        signals.ready.connect(self._on_preview_ready)
        signals.failed.connect(self._on_preview_failed)
        _PREVIEW_WORKERS.add(worker)
        _PREVIEW_POOL.start(worker)

    def _on_preview_ready(self, rel: str, image: QImage, token: int) -> None:
        self._preview_busy = False
        if self._closed:
            return
        if token == self._preview_token and rel == self._preview_rel:
            pixmap = QPixmap.fromImage(image)
            if not pixmap.isNull():
                self._preview.set_loading(False)
                self._preview.set_pixmap(pixmap)
                self._cache_preview(rel, pixmap)
        self._start_newest_pending_preview()

    def _on_preview_failed(self, rel: str, token: int) -> None:
        self._preview_busy = False
        if self._closed:
            return
        if token == self._preview_token and rel == self._preview_rel:
            self._preview.set_loading(False)
            self._preview.set_pixmap(None)
        self._start_newest_pending_preview()

    def _start_newest_pending_preview(self) -> None:
        if self._closed:
            return
        pending = self._preview_pending
        self._preview_pending = None
        if pending is None:
            return
        rel, source, token = pending
        if token != self._preview_token or rel != self._preview_rel:
            return
        self._request_preview(rel, source, token)

    def _cache_preview(self, rel: str, pixmap: QPixmap) -> None:
        self._preview_cache[rel] = pixmap
        self._preview_cache.move_to_end(rel)
        while len(self._preview_cache) > OCR_PREVIEW_CACHE_SIZE:
            self._preview_cache.popitem(last=False)

    def _clear_preview(self) -> None:
        self._preview_rel = ""
        self._preview.set_loading(False)
        self._preview.set_pixmap(None)

    def _show_message(self, message: str, *, loading: bool = False) -> None:
        self._message.setText(message)
        self._message.show()
        if loading:
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()


__all__ = ["OCR_RESULTS_PAGE_SIZE", "OcrAssetListModel", "OcrResultsPage"]
