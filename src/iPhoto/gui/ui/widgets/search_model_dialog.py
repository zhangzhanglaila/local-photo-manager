"""Model chooser for semantic search.

Shared by the first-run wizard and the settings window. It explains the same
trade-offs (language, quality, speed, download size) in one place, shows whether
the weights are already on disk, and can download them with a progress bar —
including a manual fallback (folder + model page) when the network blocks both
sources.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....agent.infrastructure.search_model_download import (
    SearchModelDownloadError,
    download_search_model,
)
from ....agent.services import search_models
from ....i18n import tr
from ..icon import load_icon
from .dialog_theme import dialog_tokens

_ACCENT_LIGHT = "#7B5E3B"
_ACCENT_DARK = "#C9A86C"
_ERROR_COLOR = "#CD4949"
_OK_COLOR = "#3E9E6A"


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


def current_device_label() -> str:
    """Return ``CUDA (…)`` / ``CPU (fp32)`` without forcing a torch import."""

    torch = sys.modules.get("torch")
    if torch is None:
        return "CPU (fp32)"
    try:
        if torch.cuda.is_available():
            return f"CUDA ({torch.cuda.get_device_name(0)})"
    except Exception:  # pragma: no cover - driver issues
        pass
    return "CPU (fp32)"


class _DownloadSignals(QObject):
    finished = Signal(bool, str)  # ok, message


class _DownloadThread(QThread):
    """Download the weights off the UI thread so the progress bar animates."""

    def __init__(self, spec: search_models.SearchModelSpec) -> None:
        super().__init__()
        self._spec = spec
        self.signals = _DownloadSignals()

    def run(self) -> None:  # pragma: no cover - exercised through the dialog
        try:
            download_search_model(self._spec)
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
        self.setFixedWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._shell = QWidget(self)
        self._shell.setObjectName("searchModelShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

        layout = QVBoxLayout(self._shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self._shell)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 14, 0)
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

        subtitle = QLabel(tr("search.model.subtitle"), self._shell)
        subtitle.setObjectName("searchModelSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setContentsMargins(24, 8, 24, 0)
        layout.addWidget(subtitle)

        self._list = QListWidget(self._shell)
        self._list.setObjectName("searchModelList")
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for spec in search_models.available_models():
            marker = model_title(spec)
            if search_models.is_installed(spec):
                marker += f"  ({tr('search.model.installed')})"
            item = QListWidgetItem(f"{marker}  ·  {model_summary(spec)}")
            item.setData(Qt.ItemDataRole.UserRole, spec.key)
            self._list.addItem(item)
            if spec.key == self._current_key:
                self._list.setCurrentItem(item)
        self._list.setMinimumHeight(140)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        list_wrap = QWidget(self._shell)
        list_layout = QVBoxLayout(list_wrap)
        list_layout.setContentsMargins(24, 12, 24, 0)
        list_layout.addWidget(self._list)
        layout.addWidget(list_wrap)

        self._details = QLabel("", self._shell)
        self._details.setObjectName("searchModelDetails")
        self._details.setWordWrap(True)
        self._details.setContentsMargins(24, 10, 24, 0)
        layout.addWidget(self._details)

        self._status = QLabel("", self._shell)
        self._status.setObjectName("searchModelStatus")
        self._status.setWordWrap(True)
        self._status.setContentsMargins(24, 6, 24, 0)
        self._status.hide()
        layout.addWidget(self._status)

        self._progress = QProgressBar(self._shell)
        self._progress.setObjectName("searchModelProgress")
        self._progress.setRange(0, 0)  # indeterminate while downloading
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setContentsMargins(24, 8, 24, 0)
        self._progress.hide()
        layout.addWidget(self._progress)

        actions = QWidget(self._shell)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(24, 18, 24, 20)
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

    # ------------------------------------------------------------------
    def selected_key(self) -> str:
        return self._selected_key

    def _spec(self) -> search_models.SearchModelSpec:
        item = self._list.currentItem()
        key = str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else self._current_key
        return search_models.resolve_spec(key)

    def _accept_selection(self) -> None:
        spec = self._spec()
        if not search_models.is_installed(spec):
            # Selecting a missing model means "get it for me".
            self._start_download()
            return
        self._selected_key = spec.key
        self.accept()

    def _on_selection_changed(self, _current, _previous) -> None:
        self._status.hide()
        self._refresh_state()

    def _refresh_state(self) -> None:
        spec = self._spec()
        installed = search_models.is_installed(spec)
        self._details.setText(model_details(spec))
        self._download_button.setVisible(not installed)
        self._use_button.setEnabled(installed)
        self._use_button.setText(
            tr("search.model.use") if installed else tr("search.model.download_and_use")
        )
        self._use_button.setVisible(True)

    # ------------------------------------------------------------------
    def _start_download(self) -> None:
        spec = self._spec()
        if self._thread is not None:
            return
        # Files copied in by hand are picked up on retry instead of downloading
        # them a second time.
        if search_models.is_installed(spec):
            self._status.setText(tr("search.model.installed"))
            self._set_status_color(_OK_COLOR)
            self._status.show()
            self._refresh_state()
            return
        self._download_button.setEnabled(False)
        self._use_button.setEnabled(False)
        self._progress.show()
        self._status.setText(tr("search.model.downloading", name=model_title(spec), mb=spec.download_mb))
        self._set_status_color(None)
        self._status.show()

        thread = _DownloadThread(spec)
        self._thread = thread
        thread.signals.finished.connect(self._on_download_finished)
        thread.start()

    def _on_download_finished(self, ok: bool, message: str) -> None:
        self._thread = None
        self._progress.hide()
        self._download_button.setEnabled(True)
        spec = self._spec()
        self._status.show()
        if ok and search_models.is_installed(spec):
            self._status.setText(tr("search.model.download_done", name=model_title(spec)))
            self._set_status_color(_OK_COLOR)
            self._refresh_state()
            self._use_button.setEnabled(True)
            return

        # Both sources failed: tell the user where to put the files and how to
        # get them, instead of leaving the dialog unresponsive.
        directory = search_models.model_dir(spec)
        self._status.setText(
            tr(
                "search.model.download_failed_hint",
                path=str(directory),
                error=message[:200] or tr("search.model.download_failed"),
            )
        )
        self._set_status_color(_ERROR_COLOR)
        self._refresh_state()
        self._download_button.setText(tr("search.model.retry"))

    def _set_status_color(self, colour: str | None) -> None:
        palette = self.palette()
        if colour is None:
            colour = palette.color(QPalette.ColorRole.WindowText).name()
            self._status.setStyleSheet(f"color: {palette.color(QPalette.ColorRole.WindowText).name()};")
            return
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
        tokens = dialog_tokens(self.palette())
        accent = _ACCENT_DARK if tokens["shell"] == "#1C1C1E" else _ACCENT_LIGHT
        return (
            "QDialog#searchModelDialog { background: transparent; }"
            f"QWidget#searchModelShell {{ background-color: {tokens['shell']};"
            f"  border-radius: 14px; border: 1px solid {tokens['divider']}; }}"
            f"QLabel#searchModelTitle {{ font-size: 16px; font-weight: 600; color: {tokens['text']}; }}"
            f"QLabel#searchModelSubtitle {{ font-size: 12px; color: {tokens['muted']}; }}"
            f"QLabel#searchModelDetails {{ font-size: 12px; color: {tokens['muted']};"
            "  min-height: 54px; }"
            f"QLabel#searchModelStatus {{ font-size: 12px; }}"
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


__all__ = [
    "SearchModelDialog",
    "current_device_label",
    "model_details",
    "model_summary",
    "model_title",
]
