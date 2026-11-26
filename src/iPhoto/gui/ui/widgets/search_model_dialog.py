"""Model chooser for semantic search.

Shared by the first-run wizard and the settings window. It explains the same
trade-offs (language, quality, speed, download size) in one place, shows whether
the weights are already on disk, and downloads them with a real percentage —
with clickable model pages and a target folder when the network is blocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....agent.infrastructure.search_model_download import (
    SearchModelDownloadError,
    download_search_model,
    model_page_urls,
)
from ....agent.services import search_models
from ....i18n import tr
from ..icon import load_icon
from .dialog_theme import dialog_tokens

_ACCENT_LIGHT = "#7B5E3B"
_ACCENT_DARK = "#C9A86C"
_ERROR_COLOR = "#CD4949"
_OK_COLOR = "#3E9E6A"
_MARGIN = 24


def model_title(spec: search_models.SearchModelSpec) -> str:
    """Product name; kept in English because it is a model identifier."""

    return {
        "clip-b32": "CLIP ViT-B/32",
        "siglip2-base": "SigLIP 2 Base",
        "chinese-clip-b16": "Chinese-CLIP ViT-B/16",
    }.get(spec.key, spec.key)


def _language_label(spec: search_models.SearchModelSpec) -> str:
    return tr(f"search.model.lang.{spec.languages}")


def _quality_label(spec: search_models.SearchModelSpec) -> str:
    return tr(f"search.model.quality.{spec.quality}")


def _speed_label(spec: search_models.SearchModelSpec) -> str:
    return tr(f"search.model.speed.{spec.speed}")


def model_summary(spec: search_models.SearchModelSpec) -> str:
    """One line for the list: language · quality · size."""

    return tr(
        "search.model.summary",
        language=_language_label(spec),
        quality=_quality_label(spec),
        mb=spec.download_mb,
    )


def model_details(spec: search_models.SearchModelSpec) -> str:
    """Multi-line explanation shown under the list."""

    state = (
        tr("search.model.installed")
        if search_models.is_installed(spec)
        else tr("search.model.missing", mb=spec.download_mb)
    )
    return tr(
        "search.model.details",
        name=model_title(spec),
        language=_language_label(spec),
        quality=_quality_label(spec),
        speed=_speed_label(spec),
        mb=spec.download_mb,
        state=state,
    )


def current_device_label(status: object | None = None) -> str:
    """Return ``CUDA`` or ``CPU`` for the running encoder.

    Only the device is shown: "float16" is the arithmetic precision, not
    something a user should have to decode in a settings row. The precision is
    still reported in the log and in the model dialog details.

    *status* is the injected search-index status when the caller has one; it
    reports the device the encoder actually chose.
    """

    from ....agent.services.ai_devices import search_device

    return search_device(status)


class _DownloadSignals(QObject):
    progress = Signal(object, str)  # fraction (float | None), message
    finished = Signal(bool, str)


class _DownloadThread(QThread):
    """Download the weights off the UI thread and report byte progress."""

    def __init__(self, spec: search_models.SearchModelSpec) -> None:
        super().__init__()
        self._spec = spec
        self.signals = _DownloadSignals()

    def run(self) -> None:  # pragma: no cover - exercised through the dialog
        def on_progress(fraction, message: str) -> None:
            self.signals.progress.emit(fraction, message)

        try:
            download_search_model(self._spec, progress=on_progress)
        except SearchModelDownloadError as exc:
            self.signals.finished.emit(False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.signals.finished.emit(False, str(exc))
        else:
            self.signals.finished.emit(True, "")


class SearchModelDialog(QDialog):
    """Pick one search model; returns the key via :meth:`selected_key`."""

    def __init__(self, parent: QWidget | None = None, *, current_key: str | None = None) -> None:
        super().__init__(parent)
        self._current_key = search_models.resolve_spec(current_key).key
        self._selected_key = self._current_key
        self._thread: _DownloadThread | None = None

        self.setObjectName("searchModelDialog")
        self.setWindowTitle(tr("search.model.title"))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(580)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._shell = QWidget(self)
        self._shell.setObjectName("searchModelShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

        layout = QVBoxLayout(self._shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- header -----------------------------------------------------
        header = QWidget(self._shell)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(_MARGIN, 20, 14, 0)
        header_layout.setSpacing(8)
        title = QLabel(tr("search.model.title"), header)
        title.setObjectName("searchModelTitle")
        header_layout.addWidget(title, 1)
        self._close_button = QToolButton(header)
        self._close_button.setObjectName("searchModelClose")
        self._close_button.setFixedSize(30, 30)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.clicked.connect(self.reject)
        header_layout.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        # -- scrollable body -------------------------------------------
        body = QWidget(self._shell)
        body.setObjectName("searchModelBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(_MARGIN, 8, _MARGIN, 0)
        body_layout.setSpacing(10)

        subtitle = QLabel(tr("search.model.subtitle"), body)
        subtitle.setObjectName("searchModelSubtitle")
        subtitle.setWordWrap(True)
        body_layout.addWidget(subtitle)

        self._list = QListWidget(body)
        self._list.setObjectName("searchModelList")
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The list never scrolls on its own: the whole dialog scrolls, so long
        # item text is not cut off by a cramped inner viewport.
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for spec in search_models.available_models():
            marker = model_title(spec)
            if search_models.is_installed(spec):
                marker += f"  ({tr('search.model.installed')})"
            item = QListWidgetItem(f"{marker}  ·  {model_summary(spec)}")
            item.setData(Qt.ItemDataRole.UserRole, spec.key)
            self._list.addItem(item)
            if spec.key == self._current_key:
                self._list.setCurrentItem(item)
        self._autosize_list()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        body_layout.addWidget(self._list)

        self._details = QLabel("", body)
        self._details.setObjectName("searchModelDetails")
        self._details.setWordWrap(True)
        body_layout.addWidget(self._details)

        self._status = QLabel("", body)
        self._status.setObjectName("searchModelStatus")
        self._status.setWordWrap(True)
        self._status.hide()
        body_layout.addWidget(self._status)

        self._links = QLabel("", body)
        self._links.setObjectName("searchModelLinks")
        self._links.setWordWrap(True)
        self._links.setTextFormat(Qt.TextFormat.RichText)
        self._links.setOpenExternalLinks(True)
        self._links.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self._links.hide()
        body_layout.addWidget(self._links)

        # Progress row inside a wrapper so the bar keeps the dialog's margins.
        self._progress_row = QWidget(body)
        progress_layout = QVBoxLayout(self._progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        self._progress_label = QLabel("", self._progress_row)
        self._progress_label.setObjectName("searchModelProgressLabel")
        self._progress_bar = QProgressBar(self._progress_row)
        self._progress_bar.setObjectName("searchModelProgress")
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self._progress_label)
        progress_layout.addWidget(self._progress_bar)
        self._progress_row.hide()
        body_layout.addWidget(self._progress_row)
        body_layout.addSpacing(8)

        # The whole body scrolls (never the model list alone): short lists are
        # shown in full, and only a dialog that would not fit the screen scrolls.
        scroll = QScrollArea(self._shell)
        scroll.setObjectName("searchModelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self._scroll = scroll

        # -- footer -----------------------------------------------------
        actions = QWidget(self._shell)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(_MARGIN, 14, _MARGIN, 20)
        actions_layout.setSpacing(8)
        self._folder_button = QPushButton(tr("search.model.open_folder"), actions)
        self._folder_button.setObjectName("searchModelSecondary")
        self._folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_button.setMinimumHeight(32)
        self._folder_button.clicked.connect(self._open_model_folder)
        actions_layout.addWidget(self._folder_button)
        actions_layout.addStretch(1)

        self._cancel_button = QPushButton(tr("dialog.cancel"), actions)
        self._cancel_button.setObjectName("searchModelSecondary")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setMinimumHeight(32)
        self._cancel_button.clicked.connect(self.reject)
        actions_layout.addWidget(self._cancel_button)

        self._download_button = QPushButton(tr("search.model.download"), actions)
        self._download_button.setObjectName("searchModelSecondary")
        self._download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_button.setMinimumHeight(32)
        self._download_button.clicked.connect(self._start_download)
        actions_layout.addWidget(self._download_button)

        self._use_button = QPushButton(tr("search.model.use"), actions)
        self._use_button.setObjectName("searchModelPrimary")
        self._use_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_button.setMinimumHeight(32)
        self._use_button.clicked.connect(self._accept_selection)
        actions_layout.addWidget(self._use_button)
        layout.addWidget(actions)

        self._apply_close_icon()
        self.setStyleSheet(self._stylesheet())
        self._refresh_state()
        self._fit()

    # ------------------------------------------------------------------
    def _autosize_list(self) -> None:
        """Grow the list to its content so it never scrolls internally.

        ``sizeHintForRow`` ignores the stylesheet padding, so the height is taken
        from the rendered item rectangle once the widget is on screen.
        """

        rows = self._list.count()
        if rows <= 0:
            return
        last = self._list.item(rows - 1)
        rect = self._list.visualItemRect(last)
        if rect.height() > 0:
            height = rect.top() + rect.height() + 10
        else:
            row_height = (self._list.sizeHintForRow(0) or 30) + 16
            height = row_height * rows + 10
        self._list.setFixedHeight(max(height, 60))

    def selected_key(self) -> str:
        return self._selected_key

    def _spec(self) -> search_models.SearchModelSpec:
        item = self._list.currentItem()
        key = str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else self._current_key
        return search_models.resolve_spec(key)

    def _accept_selection(self) -> None:
        spec = self._spec()
        if not search_models.is_installed(spec):
            self._start_download()
            return
        self._selected_key = spec.key
        self.accept()

    def _on_selection_changed(self, _current, _previous) -> None:
        self._status.hide()
        self._links.hide()
        self._progress_row.hide()
        self._refresh_state()
        self._fit()

    def _fit(self) -> None:
        """Grow to the content; only scroll when the screen cannot show it."""

        self._autosize_list()
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            limit = int(screen.availableGeometry().height() * 0.8)
            if self.height() > limit:
                self.resize(self.width(), limit)

    def _refresh_state(self) -> None:
        spec = self._spec()
        installed = search_models.is_installed(spec)
        self._details.setText(model_details(spec))
        self._download_button.setVisible(not installed)
        self._download_button.setEnabled(self._thread is None)
        self._use_button.setEnabled(installed and self._thread is None)
        self._use_button.setText(
            tr("search.model.use") if installed else tr("search.model.download_and_use")
        )

    def _set_links_visible(self, visible: bool) -> None:
        if not visible:
            self._links.hide()
            return
        spec = self._spec()
        links = " · ".join(
            f'<a href="{url}">{label}</a>' for label, url in model_page_urls(spec)
        )
        self._links.setText(tr("search.model.manual_links", links=links))
        self._links.show()

    # ------------------------------------------------------------------
    def _start_download(self) -> None:
        spec = self._spec()
        if self._thread is not None:
            return
        if search_models.is_installed(spec):
            self._status.setText(tr("search.model.installed"))
            self._set_status_color(_OK_COLOR)
            self._status.show()
            self._refresh_state()
            return

        self._download_button.setEnabled(False)
        self._use_button.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_label.setText(
            tr("search.model.downloading", name=model_title(spec), mb=spec.download_mb)
        )
        self._progress_row.show()
        self._status.hide()
        self._links.hide()

        thread = _DownloadThread(spec)
        self._thread = thread
        thread.signals.progress.connect(self._on_download_progress)
        thread.signals.finished.connect(self._on_download_finished)
        thread.start()
        self._fit()

    def _on_download_progress(self, fraction, message: str) -> None:
        if fraction is None:
            self._progress_bar.setRange(0, 0)
            return
        if self._progress_bar.maximum() == 0:
            self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(int(max(0.0, min(1.0, float(fraction))) * 1000))
        percent = int(round(float(fraction) * 100))
        self._progress_label.setText(
            tr("search.model.downloading_percent", percent=percent)
            + (f"  ·  {message}" if message else "")
        )

    def _on_download_finished(self, ok: bool, message: str) -> None:
        self._thread = None
        self._progress_row.hide()
        spec = self._spec()
        if ok and search_models.is_installed(spec):
            self._status.setText(tr("search.model.download_done", name=model_title(spec)))
            self._set_status_color(_OK_COLOR)
            self._status.show()
            self._refresh_state()
            self._use_button.setEnabled(True)
            self._fit()
            return

        directory = search_models.model_dir(spec)
        reason = _reason_text(message)
        self._status.setText(
            tr(
                "search.model.download_failed_reason",
                reason=reason,
                path=str(directory),
            )
        )
        self._set_status_color(_ERROR_COLOR)
        self._status.show()
        self._set_links_visible(True)
        self._refresh_state()
        self._download_button.setText(tr("search.model.retry"))
        self._fit()

    def _set_status_color(self, colour: str | None) -> None:
        if colour is None:
            colour = self.palette().color(QPalette.ColorRole.WindowText).name()
        self._status.setStyleSheet(f"color: {colour};")

    def _open_model_folder(self) -> None:
        spec = self._spec()
        directory = search_models.model_dir(spec)
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._autosize_list()
        self._fit()
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.window().frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )

    def _apply_close_icon(self) -> None:
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(160)
        self._close_button.setIcon(
            load_icon(
                "window.close.svg",
                color=colour.name(QColor.NameFormat.HexArgb),
                size=(12, 12),
            )
        )
        self._close_button.setIconSize(QSize(12, 12))

    def _stylesheet(self) -> str:
        tokens = dialog_tokens(self)
        accent = _ACCENT_DARK if tokens["shell"] == "#1C1C1E" else _ACCENT_LIGHT
        return (
            "QDialog#searchModelDialog { background: transparent; }"
            f"QWidget#searchModelShell {{ background-color: {tokens['shell']};"
            f"  border-radius: 14px; border: 1px solid {tokens['divider']}; }}"
            f"QLabel#searchModelTitle {{ font-size: 16px; font-weight: 600; color: {tokens['text']}; }}"
            f"QLabel#searchModelSubtitle {{ font-size: 12px; color: {tokens['muted']}; }}"
            f"QLabel#searchModelDetails {{ font-size: 12px; color: {tokens['muted']}; }}"
            "QLabel#searchModelStatus { font-size: 12px; }"
            f"QLabel#searchModelLinks {{ font-size: 12px; color: {tokens['muted']}; }}"
            f"QLabel#searchModelProgressLabel {{ font-size: 11px; color: {tokens['muted']}; }}"
            "QScrollArea#searchModelScroll, QWidget#searchModelBody { background: transparent; }"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {tokens['divider']}; border-radius: 5px;"
            "  min-height: 32px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            f"QProgressBar#searchModelProgress {{ border: none; background-color: {tokens['secondary']};"
            "  border-radius: 3px; }"
            f"QProgressBar#searchModelProgress::chunk {{ background-color: {accent}; border-radius: 3px; }}"
            f"QListWidget#searchModelList {{ border: 1px solid {tokens['divider']};"
            f"  border-radius: 10px; background-color: {tokens['field']}; padding: 4px;"
            f"  color: {tokens['text']}; font-size: 13px; outline: none; }}"
            f"QListWidget#searchModelList::item {{ padding: 8px 6px; border-radius: 6px; }}"
            f"QListWidget#searchModelList::item:selected {{ background-color: {accent};"
            "  color: #FFFFFF; }"
            f"QToolButton#searchModelClose {{ border: none; border-radius: 8px; background: transparent; }}"
            f"QToolButton#searchModelClose:hover {{ background-color: {tokens['hover']}; }}"
            f"QPushButton#searchModelSecondary {{ min-height: 32px; padding: 0 16px;"
            f"  border-radius: 8px; border: 1px solid {tokens['divider']};"
            f"  background-color: {tokens['secondary']}; color: {tokens['text']}; font-size: 12px; }}"
            f"QPushButton#searchModelSecondary:hover {{ background-color: {tokens['hover']}; }}"
            f"QPushButton#searchModelSecondary:disabled {{ color: {tokens['muted']}; }}"
            f"QPushButton#searchModelPrimary {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: none; background-color: {accent};"
            f"  color: {'#1C1C1E' if accent == _ACCENT_DARK else '#FFFFFF'};"
            "  font-size: 13px; font-weight: 600; }"
            f"QPushButton#searchModelPrimary:disabled {{ background-color: {tokens['secondary']};"
            f"  color: {tokens['muted']}; }}"
        )


def _reason_text(message: str) -> str:
    """Turn the downloader's Chinese sentence into one short reason."""

    text = (message or "").strip()
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered or "10060" in lowered:
        return tr("search.model.reason.timeout")
    if "getaddrinfo" in lowered or "11001" in lowered:
        return tr("search.model.reason.dns")
    if "connection" in lowered or "10054" in lowered or "refused" in lowered:
        return tr("search.model.reason.network")
    if "401" in lowered or "403" in lowered:
        return tr("search.model.reason.auth")
    return text[:200] or tr("search.model.reason.unknown")


__all__ = [
    "SearchModelDialog",
    "current_device_label",
    "model_details",
    "model_summary",
    "model_title",
]
