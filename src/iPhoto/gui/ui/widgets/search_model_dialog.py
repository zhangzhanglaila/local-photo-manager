"""Model chooser for semantic search.

Shared by the first-run wizard and the settings window so both explain the same
trade-offs (language, quality, speed, download size) in the same place.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....agent.services import search_models
from ....i18n import tr
from ..icon import load_icon
from .dialog_theme import dialog_tokens

_ACCENT_LIGHT = "#7B5E3B"
_ACCENT_DARK = "#C9A86C"


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


class SearchModelDialog(QDialog):
    """Pick one search model; returns the key via :meth:`selected_key`."""

    def __init__(self, parent: QWidget | None = None, *, current_key: str | None = None) -> None:
        super().__init__(parent)
        self._current_key = search_models.resolve_spec(current_key).key
        self._selected_key = self._current_key

        self.setObjectName("searchModelDialog")
        self.setWindowTitle(tr("search.model.title"))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(520)

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
        for spec in search_models.available_models():
            item = QListWidgetItem(f"{model_title(spec)}  ·  {model_summary(spec)}")
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

        actions = QWidget(self._shell)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(24, 18, 24, 20)
        actions_layout.setSpacing(8)
        actions_layout.addStretch(1)
        cancel = QPushButton(tr("dialog.cancel"), actions)
        cancel.setObjectName("searchModelSecondary")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setMinimumHeight(32)
        cancel.clicked.connect(self.reject)
        actions_layout.addWidget(cancel)
        self._use_button = QPushButton(tr("search.model.use"), actions)
        self._use_button.setObjectName("searchModelPrimary")
        self._use_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_button.setMinimumHeight(32)
        self._use_button.clicked.connect(self._accept_selection)
        actions_layout.addWidget(self._use_button)
        layout.addWidget(actions)

        self._apply_close_icon()
        self.setStyleSheet(self._stylesheet())
        self._on_selection_changed(self._list.currentItem(), None)

    # ------------------------------------------------------------------
    def selected_key(self) -> str:
        return self._selected_key

    def _accept_selection(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._selected_key = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        spec = search_models.resolve_spec(str(current.data(Qt.ItemDataRole.UserRole)))
        self._details.setText(model_details(spec))
        self._use_button.setEnabled(spec.key != self._current_key)

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

    # ------------------------------------------------------------------
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
            f"QListWidget#searchModelList {{ border: 1px solid {tokens['divider']};"
            f"  border-radius: 10px; background-color: {tokens['field']}; padding: 4px;"
            f"  color: {tokens['text']}; font-size: 13px; outline: none; }}"
            f"QListWidget#searchModelList::item {{ padding: 8px 6px; border-radius: 6px; }}"
            f"QListWidget#searchModelList::item:selected {{ background-color: {accent};"
            "  color: #FFFFFF; }"
            f"QToolButton#searchModelClose {{ border: none; border-radius: 8px; background: transparent; }}"
            f"QToolButton#searchModelClose:hover {{ background-color: {tokens['hover']}; }}"
            f"QPushButton#searchModelSecondary {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: 1px solid {tokens['divider']};"
            f"  background-color: {tokens['secondary']}; color: {tokens['text']}; font-size: 13px; }}"
            f"QPushButton#searchModelSecondary:hover {{ background-color: {tokens['hover']}; }}"
            f"QPushButton#searchModelPrimary {{ min-height: 32px; padding: 0 18px;"
            f"  border-radius: 8px; border: none; background-color: {accent};"
            f"  color: {'#1C1C1E' if accent == _ACCENT_DARK else '#FFFFFF'};"
            "  font-size: 13px; font-weight: 600; }"
            f"QPushButton#searchModelPrimary:disabled {{ background-color: {tokens['secondary']};"
            f"  color: {tokens['muted']}; }}"
        )


def current_device_label() -> str:
    """Return ``CUDA (fp16)`` / ``CPU (fp32)`` without forcing a torch import.

    Importing torch costs seconds and ~700 MB, so the label reports the device
    only once the app has already loaded it (first search or first index run).
    """

    import sys

    torch = sys.modules.get("torch")
    if torch is None:
        return "CPU (fp32)"
    try:
        if torch.cuda.is_available():
            return f"CUDA ({torch.cuda.get_device_name(0)})"
    except Exception:  # pragma: no cover - driver issues
        pass
    return "CPU (fp32)"


__all__ = [
    "SearchModelDialog",
    "current_device_label",
    "model_details",
    "model_summary",
    "model_title",
]
