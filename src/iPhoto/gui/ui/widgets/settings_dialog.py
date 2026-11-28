"""Standalone settings window.

The toolbar used to hide every application option behind a "..." menu. Settings
now opens the same options in a dedicated window, drawn like the reference
application: a rounded frameless card with a drop shadow, a floating close
button, a dimmed main window behind it, an icon navigation column on the left
and one settings page on the right. Clicking the dim layer or pressing Escape
dismisses the card with a short fade.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....agent.services import (
    ai_features,
    face_models,
    ocr_models,
    search_models,
)
from ....agent.services.ai_devices import onnx_device
from ....i18n import tr
from ..icon import load_icon
from .search_model_dialog import SearchModelDialog, model_title
from .spinner import Spinner

_ACCELERATOR_RE = re.compile(r"\s*\(&[A-Za-z0-9]\)\s*$")

#: ``FaceModelSpec.speed`` is a 1-3 rank; the translations are named.
_SPEED_KEYS = {1: "medium", 2: "fast", 3: "fastest"}

_ACCENT_LIGHT = "#7B5E3B"
_ACCENT_DARK = "#C9A86C"

#: Card geometry and the transparent ring reserved for its drop shadow.
_CARD_WIDTH = 1000
_CARD_HEIGHT = 760
_CARD_MIN_WIDTH = 760
_CARD_MIN_HEIGHT = 520
_SHADOW_MARGIN = 28

#: One 16 px line icon per navigation page, in nav order.
_NAV_ICONS = (
    "settings.general.svg",
    "settings.browsing.svg",
    "settings.export.svg",
    "settings.library.svg",
    "settings.intelligence.svg",
)

#: Offscreen test runs cannot drive QPropertyAnimation; keep them instant.
_ANIMATIONS = os.environ.get("QT_QPA_PLATFORM", "").casefold() != "offscreen"


class _AsyncValue(QWidget):
    """A value row that spins until its background probe returns."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.spinner = Spinner(14, self)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel("", self)
        self.label.setObjectName("settingsPathLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label.setWordWrap(False)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label.hide()
        self.setToolTip(tr("settings.loading"))
        self.spinner.start()

    def set_value(self, text: str) -> None:
        if self.label.text() != text:
            self.label.setText(text)
            self.label.setVisible(bool(text))
        if self.toolTip():
            self.setToolTip("")
        if self.spinner.isVisible():
            self.spinner.stop()
            self.spinner.hide()

    def set_loading(self) -> None:
        self.label.clear()
        self.label.hide()
        self.setToolTip(tr("settings.loading"))
        self.spinner.show()
        self.spinner.start()

    def text(self) -> str:
        return self.label.text()


class _ProbeHub(QObject):
    """Long-lived signal source for settings probes.

    A probe can outlive the dialog that started it (the user closes 设置 while a
    database read is still running). Using the window as the signal source made
    Qt raise ``Signal source has been deleted``; this hub lives for the whole
    process and the dialog filters results by worker identity.
    """

    meta_ready = Signal(object, object)
    meta_failed = Signal(object, str)
    ai_ready = Signal(object, object)
    ai_failed = Signal(object, str)


_PROBE_HUB: _ProbeHub | None = None
#: Keep Python wrappers alive while their C++ QRunnable is still in the pool.
_ACTIVE_PROBES: set[QRunnable] = set()


def _probe_hub() -> _ProbeHub:
    """Return a hub parented to the live application.

    Creating a module-level ``QObject`` before ``QApplication`` exists made Qt
    delete it later, which surfaced as ``Signal source has been deleted`` from a
    probe thread. The hub is therefore created on first use and parented to the
    current application.
    """

    global _PROBE_HUB
    if _PROBE_HUB is not None:
        try:
            import shiboken6

            if shiboken6.isValid(_PROBE_HUB):
                return _PROBE_HUB
        except (ImportError, RuntimeError):  # shiboken is always present in practice
            return _PROBE_HUB
    app = QCoreApplication.instance()
    _PROBE_HUB = _ProbeHub(app)
    return _PROBE_HUB


class _MetaProbe(QRunnable):
    """Resolve model names, devices and the search index off the GUI thread.

    ``onnxruntime``/``torch`` imports and the model-directory scans are the
    expensive part of opening settings; doing them here keeps the page
    interactive while the value rows show their spinner.
    """

    def __init__(self, root, keys: dict, status: dict) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self._keys = dict(keys)
        self._status = dict(status)

    def run(self) -> None:
        try:
            from ....agent.services import face_models, ocr_models, search_models
            from ....agent.services.ai_devices import onnx_device, search_device
            from ....agent.services.search_coverage import compute_coverage

            device = onnx_device()
            search_spec = search_models.resolve_spec(self._keys.get("search"))
            search_device_text = self._status.get("device") or ""
            if not search_device_text:
                search_device_text = search_device(None)
            coverage = None
            if (
                self._root is not None
                and not self._status.get("running")
                and not self._status.get("total")
            ):
                try:
                    coverage = compute_coverage(
                        self._root, search_spec.dir_name
                    )
                except (OSError, RuntimeError, sqlite3.Error, ValueError):
                    coverage = None

            packs = {}
            for feature, registry in (("face", face_models), ("ocr", ocr_models)):
                spec = registry.resolve_spec(self._keys.get(feature))
                packs[feature] = {
                    "title": spec.title,
                    "installed": bool(registry.is_installed(spec)),
                }

            _probe_hub().meta_ready.emit(
                self,
                {
                    "device": device,
                    "search": {
                        "title": model_title(search_spec),
                        "installed": bool(search_models.is_installed(search_spec)),
                        "device": str(search_device_text).upper(),
                        "coverage": coverage,
                    },
                    "packs": packs,
                    "status": dict(self._status),
                },
            )
        except (
            ImportError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValueError,
        ) as exc:  # pragma: no cover - display only
            _probe_hub().meta_failed.emit(self, str(exc))
        finally:
            _ACTIVE_PROBES.discard(self)


class _AiProbe(QRunnable):
    """Read face/OCR coverage and the people count without blocking the UI."""

    def __init__(self, root, *, include_people: bool) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self._include_people = bool(include_people)

    def run(self) -> None:
        try:
            if self._root is None:
                _probe_hub().ai_ready.emit(
                    self, {"face": (0, 0), "ocr": (0, 0), "people": None}
                )
                return
            from ....agent.services.ai_coverage import (
                face_coverage,
                ocr_coverage,
                people_count,
            )

            data = {
                "face": face_coverage(self._root),
                "ocr": ocr_coverage(self._root),
                "people": None,
            }
            if self._include_people:
                data["people"] = people_count(self._root)
            _probe_hub().ai_ready.emit(self, data)
        except (
            ImportError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValueError,
        ) as exc:  # pragma: no cover - display only
            _probe_hub().ai_failed.emit(self, str(exc))
        finally:
            _ACTIVE_PROBES.discard(self)


def _plain(text: str) -> str:
    """Return a menu label without its ampersand accelerator."""

    return _ACCELERATOR_RE.sub("", text).replace("&", "")


_PAGE_COLUMN_WIDTH = 680
_AI_PAGE_INDEX = 4


class _PageColumn(QWidget):
    """A 680 px content column that shrinks with the window and stays centred."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt style
        hint = super().sizeHint()
        return QSize(_PAGE_COLUMN_WIDTH, hint.height())


class _SettingsSwitch(QCheckBox):
    """A small iOS-style switch used for every boolean settings row.

    QCheckBox is kept as the base class so the existing action wiring, keyboard
    handling (Space) and ``isChecked()`` checks keep working; only the painting
    is replaced.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 36x20 track + a 2 px ring so the focus outline is never clipped.
        self.setFixedSize(40, 24)
        self._offset = 1.0 if self.isChecked() else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt style
        return QSize(40, 24)

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802 - Qt style
        # The whole switch should be clickable, not just the tiny indicator.
        return self.rect().contains(pos)

    def sync_offset(self) -> None:
        """Snap the knob to the current state without animating."""

        self._anim.stop()
        self._offset = 1.0 if self.isChecked() else 0.0
        self.update()

    def _on_anim(self, value: object) -> None:
        self._offset = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        if not _ANIMATIONS:
            self.sync_offset()
            return
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt style
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(0.45)

        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        accent = QColor(_ACCENT_DARK if dark else _ACCENT_LIGHT)
        if self.isChecked():
            track = accent
            border = QColor(0, 0, 0, 0)
            knob = QColor("#FFFFFF")
        else:
            track = QColor(255, 255, 255, 24) if dark else QColor(0, 0, 0, 16)
            border = QColor(255, 255, 255, 44) if dark else QColor(0, 0, 0, 28)
            knob = QColor(180, 180, 186) if dark else QColor(142, 142, 160)

        track_rect = QRectF(2.5, 2.5, 35.0, 19.0)
        painter.setPen(QPen(border, 1) if border.alpha() else Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(track_rect, 9.5, 9.5)

        knob_x = 4.0 + 16.0 * self._offset
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 36))
        painter.drawEllipse(QRectF(knob_x, 5.5, 14.0, 14.0))
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(knob_x, 5.0, 14.0, 14.0))

        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 170), 1.5))
            painter.drawRoundedRect(QRectF(0.75, 0.75, 38.5, 22.5), 11.0, 11.0)


class _SettingsScrim(QWidget):
    """The dim layer between the application and the settings card."""

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._opacity = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.hide()

    def _on_anim(self, value: object) -> None:
        self._opacity = float(value)
        self.update()

    def fade_in(self, *, animate: bool = True) -> None:
        self.show()
        self.raise_()
        if not animate:
            self._anim.stop()
            self._opacity = 1.0
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def fade_out(self, *, animate: bool = True, on_done=None) -> None:
        if not animate:
            self._anim.stop()
            self._opacity = 0.0
            self.hide()
            if on_done is not None:
                on_done()
            return
        self._anim.stop()
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(0.0)

        def _finished() -> None:
            self._anim.finished.disconnect(_finished)
            self.hide()
            if on_done is not None:
                on_done()

        self._anim.finished.connect(_finished)
        self._anim.start()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt style
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, int(102 * self._opacity)))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt style
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _SettingsNavDelegate(QStyledItemDelegate):
    """Rounded navigation rows with icons and an animated accent bar."""

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._view = view
        self._accent_row = -1
        self._accent_scale = 1.0
        self._accent_anim = QVariantAnimation(self)
        self._accent_anim.setDuration(180)
        self._accent_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._accent_anim.valueChanged.connect(self._on_accent_value)

    def _on_accent_value(self, value: object) -> None:
        self._accent_scale = float(value)
        self._view.viewport().update()

    def set_selected_row(self, row: int, *, animate: bool = True) -> None:
        """Grow the accent bar for *row*; the previous row simply loses it."""

        if row == self._accent_row:
            return
        self._accent_row = row
        self._accent_anim.stop()
        if not animate or not _ANIMATIONS:
            self._accent_scale = 1.0
            self._view.viewport().update()
            return
        self._accent_scale = 0.0
        self._accent_anim.setStartValue(0.0)
        self._accent_anim.setEndValue(1.0)
        self._accent_anim.start()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), 40)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        dark = option.palette.color(QPalette.ColorRole.Window).lightness() < 128

        pill = QColor("#ECECEC") if not dark else QColor(255, 255, 255, 30)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                pill if selected else QColor(pill.red(), pill.green(), pill.blue(), 130)
            )
            painter.drawRoundedRect(QRectF(rect).adjusted(0, 1, 0, -1), 8, 8)

        colour = QColor(option.palette.color(QPalette.ColorRole.Text))
        if not selected:
            colour.setAlpha(170)

        if selected:
            accent = QColor(_ACCENT_DARK if dark else _ACCENT_LIGHT)
            height = 16.0 * (self._accent_scale if index.row() == self._accent_row else 1.0)
            if height > 0.1:
                painter.setBrush(accent)
                painter.setPen(Qt.PenStyle.NoPen)
                bar = QRectF(
                    rect.left(),
                    rect.center().y() - height / 2.0,
                    3.0,
                    height,
                )
                painter.drawRoundedRect(bar, 1.5, 1.5)

        row = index.row()
        icon_x = rect.left() + 16
        if 0 <= row < len(_NAV_ICONS):
            icon = load_icon(
                _NAV_ICONS[row],
                color=colour.name(QColor.NameFormat.HexArgb),
                size=(16, 16),
            )
            painter.drawPixmap(QPoint(icon_x, rect.center().y() - 8), icon.pixmap(16, 16))
            text_left = icon_x + 16 + 10
        else:
            text_left = icon_x

        font = QFont(option.font)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(colour)
        text_rect = rect.adjusted(text_left - rect.left(), 0, -8, 0)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        elided = QFontMetrics(font).elidedText(
            text, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )
        painter.restore()


class _SettingsTitleBar(QWidget):
    """Invisible drag strip; the close button floats over the card instead."""

    def __init__(self, dialog: "SettingsDialog") -> None:
        super().__init__(dialog)
        self.setObjectName("settingsTitleBar")
        self.setFixedHeight(32)
        self._dialog = dialog
        self._press_offset: QPoint | None = None

    # Frameless windows are moved by dragging their title row.
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._press_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_offset = None
        super().mouseReleaseEvent(event)


class _ActionRow(QWidget):
    """Clickable settings row that runs an action (chevron affordance)."""

    activated = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsActionRow")
        self.setMinimumHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(12)

        label = QLabel(text, self)
        label.setObjectName("settingsRowLabel")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(label, 1)

        chevron = QLabel(self)
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(110)
        chevron.setPixmap(
            load_icon(
                "chevron.right.svg",
                color=colour.name(QColor.NameFormat.HexArgb),
                size=(12, 12),
            ).pixmap(12, 12)
        )
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(chevron, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.activated.emit()
        super().mouseReleaseEvent(event)


class SettingsDialog(QDialog):
    """The application's settings, grouped into pages."""

    def __init__(
        self,
        header,
        library=None,
        parent: QWidget | None = None,
        settings=None,
        search_status=None,
        model_selection=None,
    ) -> None:
        super().__init__(parent)
        self._header = header
        self._library = library
        self._settings = settings
        self._search_status = search_status
        self._model_selection = model_selection
        self._pages: list[QWidget] = []
        self._ocr_results_page = None
        self._meta_worker = None
        self._ai_worker = None
        self._meta_pending = False
        self._ai_pending = False
        self._meta_values: dict | None = None
        self._ai_values: dict | None = None
        self._closed = False
        self._ai_probes_started = False
        #: Coverage is read from the AI databases, which change underneath the
        #: dialog while a face/OCR scan runs. A light poll keeps "processed /
        #: total" moving; each poll runs off the GUI thread.
        self._ai_refresh_timer = QTimer(self)
        self._ai_refresh_timer.setInterval(1000)
        self._ai_refresh_timer.timeout.connect(self._refresh_ai_rows)
        self._people_cache: tuple[str, float, int] | None = None
        self._hub = _probe_hub()
        self._hub.meta_ready.connect(self._on_meta_ready)
        self._hub.meta_failed.connect(self._on_meta_failed)
        self._hub.ai_ready.connect(self._on_ai_ready)
        self._hub.ai_failed.connect(self._on_ai_failed)

        self.setWindowTitle(tr("dialog.settings_title"))
        self.setObjectName("settingsDialog")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(_CARD_WIDTH + 2 * _SHADOW_MARGIN, _CARD_HEIGHT + 2 * _SHADOW_MARGIN)
        self.setMinimumSize(
            _CARD_MIN_WIDTH + 2 * _SHADOW_MARGIN,
            _CARD_MIN_HEIGHT + 2 * _SHADOW_MARGIN,
        )

        #: The scrim lives in the main window, not in this top-level window.
        self._scrim: _SettingsScrim | None = None
        self._scrim_parent = parent if isinstance(parent, QWidget) else None
        self._allow_close = False
        self._close_anim: QParallelAnimationGroup | None = None
        self._open_anim: QParallelAnimationGroup | None = None
        self._opening = False
        self._closing = False
        self._open_target: QPoint | None = None
        self._opening_guard = QTimer(self)
        self._opening_guard.setSingleShot(True)
        self._opening_guard.timeout.connect(self._end_open_guard)
        self._close_guard = QTimer(self)
        self._close_guard.setSingleShot(True)
        self._close_guard.timeout.connect(self._finish_close)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        #: Full-window host: the shell sits inside it with a transparent ring
        #: reserved for the drop shadow, so the shadow never gets clipped.
        self._card_host = QWidget(self)
        self._card_host.setObjectName("settingsCardHost")
        outer.addWidget(self._card_host)

        self._shadow_panel = QWidget(self._card_host)
        self._shadow_panel.setObjectName("settingsShadowPanel")
        self._shadow_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._shadow_panel.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        shadow = QGraphicsDropShadowEffect(self._shadow_panel)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 60))
        self._shadow_effect = shadow
        self._shadow_panel.setGraphicsEffect(shadow)
        # A QGraphicsDropShadowEffect re-blurs the whole card on every repaint.
        # While the user scrolls a settings page that costs ~20 ms per step
        # (measured on the 智能 page), so suspend the effect during the gesture
        # and restore it shortly after the scrollbar stops.
        self._shadow_idle_timer = QTimer(self)
        self._shadow_idle_timer.setSingleShot(True)
        self._shadow_idle_timer.setInterval(120)
        self._shadow_idle_timer.timeout.connect(self._restore_shadow)

        self._shell = QWidget(self._card_host)
        self._shell.setObjectName("settingsShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.close_button = QToolButton(self._shell)
        self.close_button.setObjectName("settingsCloseButton")
        glyph = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        glyph.setAlpha(160)
        self.close_button.setIcon(
            load_icon(
                "window.close.svg",
                color=glyph.name(QColor.NameFormat.HexArgb),
                size=(16, 16),
            )
        )
        self.close_button.setIconSize(QSize(16, 16))
        self.close_button.setFixedSize(32, 32)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip(tr("window.close"))
        self.close_button.clicked.connect(self.close)
        self.close_button.raise_()

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self._title_bar = _SettingsTitleBar(self)
        shell_layout.addWidget(self._title_bar)

        body = QWidget(self._shell)
        body.setObjectName("settingsBody")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._nav = QListWidget(body)
        self._nav.setObjectName("settingsNav")
        self._nav.setFixedWidth(220)
        self._nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._nav_delegate = _SettingsNavDelegate(self._nav)
        self._nav.setItemDelegate(self._nav_delegate)
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setSpacing(2)
        self._nav.setMouseTracking(True)
        self._nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_layout.addWidget(self._nav)

        self._stack = QStackedWidget(body)
        self._stack.setObjectName("settingsStack")
        body_layout.addWidget(self._stack, 1)
        shell_layout.addWidget(body, 1)

        self._build_pages()
        self._nav.currentRowChanged.connect(self._nav_delegate.set_selected_row)
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        # Clicking the already-selected 智能 row while the OCR browser is open
        # does not emit currentRowChanged; itemClicked still returns to settings.
        self._nav.itemClicked.connect(
            lambda item: self._stack.setCurrentIndex(self._nav.row(item))
        )
        self._nav.setCurrentRow(0)
        self._nav_delegate.set_selected_row(0, animate=False)
        self.setStyleSheet(self._stylesheet())
        self._sync_card_geometry()
        # Do not probe face/OCR/search state here: resolving the device imports
        # onnxruntime and torch (about 0.7 s) and the import lock starves the GUI
        # thread, so the first paint of the settings window used to wait for it.
        # Probes start when 智能 is opened for the first time (see _on_nav_changed).

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def _build_pages(self) -> None:
        header = self._header
        root = self._library.root() if self._library is not None else None

        general = self._page(
            tr("settings.page.general"),
            [
                (
                    tr("settings.section.interface"),
                    [
                        self._value_row(tr("settings.row.appearance"), self._combo(header.theme_group.actions())),
                        self._value_row(tr("settings.row.language"), self._combo(header.language_group.actions())),
                    ],
                ),
                (tr("settings.section.window"), [self._value_row(tr("settings.row.close_action"), self._close_action_combo())]),
            ],
        )

        browsing = self._page(
            tr("settings.page.browsing"),
            [
                (
                    tr("settings.section.display"),
                    [
                        self._value_row(
                            tr("settings.row.filmstrip"), self._checkbox(header.toggle_filmstrip_action)
                        ),
                        self._value_row(
                            tr("settings.row.face_names"),
                            self._checkbox(header.toggle_face_names_action),
                        ),
                        self._value_row(
                            tr("settings.row.hidden_people"),
                            self._checkbox(header.toggle_hidden_people_action),
                        ),
                    ],
                ),
                (
                    tr("settings.section.interaction"),
                    [
                        self._value_row(
                            tr("settings.row.wheel"), self._combo(header.wheel_action_group.actions())
                        ),
                        self._value_row(
                            tr("settings.row.share"), self._combo(header.share_action_group.actions())
                        ),
                    ],
                ),
            ],
        )

        exporting = self._page(
            tr("settings.page.export"),
            [
                (
                    tr("settings.section.defaults"),
                    [
                        self._value_row(
                            tr("settings.row.export_destination"),
                            self._combo(header.export_destination_group.actions()),
                        ),
                        self._value_row(
                            tr("settings.row.export_format"),
                            self._combo(header.export_format_group.actions()),
                        ),
                    ],
                ),
                (
                    tr("settings.section.actions"),
                    [
                        self._action_row(tr("settings.row.export_all_edited"), header.export_all_edited_action),
                        self._action_row(tr("settings.row.export_selected"), header.export_selected_action),
                    ],
                ),
            ],
        )

        library = self._page(
            tr("settings.page.library"),
            [
                (
                    tr("settings.section.basic_library"),
                    [
                        self._value_row(tr("settings.row.current_library"), self._path_label(root)),
                        self._action_row(tr("settings.row.bind_library"), header.bind_library_action),
                    ],
                ),
                (tr("settings.section.storage"), [self._action_row(tr("settings.row.change_workspace"), header.change_workspace_action)]),
                (
                    tr("settings.section.maintenance"),
                    [
                        self._action_row(tr("settings.row.run_setup"), header.run_setup_action),
                        self._action_row(tr("settings.row.open_album_folder"), header.open_album_action),
                        self._action_row(tr("settings.row.rebuild_links"), header.rebuild_links_action),
                        self._action_row(tr("settings.row.download_map"), header.download_map_extension_action),
                    ],
                ),
            ],
        )

        intelligence = self._page(
            tr("settings.page.intelligence"),
            [
                (
                    tr("settings.section.semantic"),
                    [
                        self._value_row(
                            tr("settings.row.semantic_search"),
                            self._checkbox(header.toggle_semantic_search_action),
                        ),
                        self._value_row(
                            tr("settings.row.search_model"),
                            self._search_model_value(),
                        ),
                        self._value_row(
                            tr("settings.row.search_index_state"),
                            self._index_state_value(),
                        ),
                        self._action_row(
                            tr("search.model.switch"), self._switch_model_action()
                        ),
                        self._action_row(
                            tr("search.model.rebuild"), self._rebuild_index_action()
                        ),
                        self._value_row(
                            tr("settings.row.search_precision"),
                            self._combo(
                                [
                                    header.search_precision_actions[tier]
                                    for tier in ("strict", "balanced", "loose")
                                ]
                            ),
                        ),
                    ],
                ),
                (
                    tr("settings.section.faces"),
                    [
                        self._value_row(
                            tr("settings.row.face_enable"),
                            self._ai_toggle(ai_features.FACE_KEY, True),
                        ),
                        self._value_row(
                            tr("settings.row.face_model"),
                            self._packs_value("face"),
                        ),
                        self._action_row(
                            tr("settings.row.face_model_switch"),
                            self._switch_packs_action("face"),
                        ),
                        self._value_row(
                            tr("settings.row.face_index_state"),
                            self._ai_state_value("face"),
                        ),
                        self._action_row(
                            tr("settings.row.face_rebuild"),
                            self._ai_rebuild_action("face"),
                        ),
                    ],
                ),
                (
                    tr("settings.section.ocr"),
                    [
                        self._value_row(
                            tr("settings.row.ocr"),
                            self._ai_toggle(ai_features.OCR_KEY, True),
                        ),
                        self._value_row(
                            tr("settings.row.ocr_model"),
                            self._packs_value("ocr"),
                        ),
                        self._action_row(
                            tr("settings.row.ocr_model_switch"),
                            self._switch_packs_action("ocr"),
                        ),
                        self._value_row(
                            tr("settings.row.ocr_index_state"),
                            self._ai_state_value("ocr"),
                        ),
                        self._action_row(
                            tr("settings.row.ocr_rebuild"),
                            self._ai_rebuild_action("ocr"),
                        ),
                        self._action_row(
                            tr("settings.row.ocr_results"),
                            self._open_ocr_results_action(),
                        ),
                    ],
                ),
                (
                    tr("settings.section.tools"),
                    [
                        self._action_row(tr("settings.row.find_duplicates"), header.find_duplicates_action),
                        self._action_row(tr("settings.row.smart_event"), header.smart_album_event_action),
                        self._action_row(tr("settings.row.smart_location"), header.smart_album_location_action),
                        self._action_row(tr("settings.row.smart_time"), header.smart_album_time_action),
                        self._action_row(tr("settings.row.smart_theme"), header.smart_album_theme_action),
                    ],
                ),
            ],
        )

        for title, page in (
            (tr("settings.page.general"), general),
            (tr("settings.page.browsing"), browsing),
            (tr("settings.page.export"), exporting),
            (tr("settings.page.library"), library),
            (tr("settings.page.intelligence"), intelligence),
        ):
            self._pages.append(page)
            self._stack.addWidget(page)
            QListWidgetItem(title, self._nav)

    def _page(self, title: str, sections: list[tuple[str, list[QWidget]]]) -> QWidget:
        """Build one settings page out of labelled section cards.

        A settings page is a list of grouped rows, not a column of buttons: each
        section is a rounded card whose rows carry the label on the left and the
        control (or a chevron for actions) on the right.
        """

        # The cards keep their natural height and the page scrolls: squeezing a
        # five-row card into a fixed page compressed every row and made the
        # section look cramped.
        content = QWidget(self)
        content.setObjectName("settingsPageContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QHBoxLayout(content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        column = _PageColumn(content)
        column.setObjectName("settingsPageColumn")
        column.setMaximumWidth(_PAGE_COLUMN_WIDTH)
        column.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        outer.addStretch(1)
        outer.addWidget(column, 0)
        outer.addStretch(1)

        layout = QVBoxLayout(column)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(6)

        heading = QLabel(title, column)
        heading.setObjectName("settingsPageTitle")
        layout.addWidget(heading)
        layout.addSpacing(12)

        for section_title, rows in sections:
            if section_title:
                label = QLabel(section_title, content)
                label.setObjectName("settingsSectionLabel")
                layout.addWidget(label)
            layout.addWidget(self._card(rows, content))
            layout.addSpacing(14)
        layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.verticalScrollBar().valueChanged.connect(self._on_page_scrolled)

        page = QWidget(self)
        page.setObjectName("settingsPage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addWidget(scroll)
        return page

    def _card(self, rows: list[QWidget], parent: QWidget) -> QWidget:
        card = QWidget(parent)
        card.setObjectName("settingsCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for index, row in enumerate(rows):
            if index:
                line = QWidget(card)
                line.setObjectName("settingsDivider")
                line.setFixedHeight(1)
                layout.addWidget(line)
            layout.addWidget(row)
        return card

    def _value_row(self, title: str, control: QWidget) -> QWidget:
        row = QWidget(self)
        row.setObjectName("settingsRow")
        row.setMinimumHeight(48)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(16)

        label = QLabel(title, row)
        label.setObjectName("settingsRowLabel")
        layout.addWidget(label, 1)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight)
        return row

    def _action_row(self, title: str, action: QAction) -> QWidget:
        row = _ActionRow(title, self)
        row.activated.connect(action.trigger)
        return row

    def _checkbox(self, action: QAction) -> QCheckBox:
        box = _SettingsSwitch(self)
        box.setChecked(action.isChecked())
        box.sync_offset()
        box.toggled.connect(action.setChecked)
        action.toggled.connect(box.setChecked)
        return box

    def _combo(self, actions) -> QComboBox:
        actions = list(actions)
        combo = QComboBox(self)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.view().setCursor(Qt.CursorShape.PointingHandCursor)
        for action in actions:
            combo.addItem(_plain(action.text()))
        checked = next((i for i, action in enumerate(actions) if action.isChecked()), 0)
        combo.setCurrentIndex(checked)
        combo.currentIndexChanged.connect(lambda index: actions[index].trigger())
        return combo

    def _index_state_value(self) -> _AsyncValue:
        """Live coverage / rebuild progress for the active model."""

        label = _AsyncValue(self)
        self._index_state_label = label
        status = self._search_status
        if status is not None:
            status.changed.connect(self._refresh_search_rows)
            self.finished.connect(self._disconnect_status)
        return label

    def _disconnect_status(self, _result: int = 0) -> None:
        status = self._search_status
        if status is None:
            return
        try:
            status.changed.disconnect(self._refresh_search_rows)
        except (RuntimeError, TypeError):
            pass

    def _refresh_search_rows(self) -> None:
        """Ask the background probe to refresh model/index rows.

        The status signal can fire on every indexing tick; coalescing here avoids
        starting one thread per tick while still keeping the labels live.
        """

        self._schedule_meta_probe()

    def _apply_meta_values(self, values: dict) -> None:
        search = values.get("search") or {}
        label = getattr(self, "_model_value_label", None)
        if label is not None:
            suffix = (
                ""
                if search.get("installed")
                else tr("search.model.not_installed_suffix")
            )
            label.set_value(
                f"{search.get('title', '')}{suffix} · {search.get('device', '')}"
            )

        state_label = getattr(self, "_index_state_label", None)
        if state_label is not None:
            status = values.get("status") or {}
            running = bool(status.get("running"))
            done = int(status.get("done") or 0)
            total = int(status.get("total") or 0)
            coverage = search.get("coverage")
            if not running and total == 0 and coverage:
                done, total = coverage
            if running and total:
                state_label.set_value(
                    tr(
                        "search.model.indexing_now",
                        percent=int(done * 100 / total),
                        done=done,
                        total=total,
                    )
                )
            elif total:
                state_label.set_value(
                    tr("search.model.coverage", done=done, total=total)
                )
            else:
                state_label.set_value(tr("search.model.coverage_unknown"))

        device = values.get("device") or ""
        for feature, pack in (values.get("packs") or {}).items():
            pack_label = getattr(self, "_pack_labels", {}).get(feature)
            if pack_label is None:
                continue
            suffix = (
                "" if pack.get("installed") else tr("search.model.not_installed_suffix")
            )
            pack_label.set_value(f"{pack.get('title', '')}{suffix} · {device}")

    def _packs_registry(self, feature: str):
        return face_models if feature == "face" else ocr_models

    def _active_spec(self, feature: str):
        """Active pack for *feature* from the injected selection.

        Falls back to the settings value (then the registry default) when the
        dialog is built without a runtime context, as some previews do.
        """

        selection = self._model_selection
        if selection is not None:
            return selection.spec(feature)
        registry = search_models if feature == "search" else self._packs_registry(feature)
        if self._settings is not None:
            return registry.resolve_spec(
                self._settings.get(registry.SETTINGS_KEY, None)
            )
        return registry.resolve_spec(None)

    def _apply_selection(self, feature: str, key: str | None) -> None:
        selection = self._model_selection
        if selection is not None:
            selection.apply(feature, key)

    def _packs_value(self, feature: str) -> _AsyncValue:
        """Show the selected pack as a value row, like 搜索模型 does."""

        label = _AsyncValue(self)
        if not hasattr(self, "_pack_labels"):
            self._pack_labels: dict[str, _AsyncValue] = {}
        self._pack_labels[feature] = label
        return label

    def _refresh_pack_rows(self) -> None:
        self._schedule_meta_probe()

    def _switch_packs_action(self, feature: str) -> QAction:
        registry = self._packs_registry(feature)
        title = tr(
            "settings.row.face_model_switch"
            if feature == "face"
            else "settings.row.ocr_model_switch"
        )
        action = QAction(title, self)

        def _run() -> None:
            from .model_chooser_dialog import ModelChooserDialog, ModelOption

            options = [
                ModelOption(
                    key=spec.key,
                    title=spec.title,
                    summary=spec.summary,
                    details=self._pack_details(feature, spec),
                    installed=registry.is_installed(spec),
                )
                for spec in registry.available_models()
            ]
            current = registry.resolve_spec(
                self._settings.get(registry.SETTINGS_KEY, None)
                if self._settings is not None
                else None
            )
            dialog = ModelChooserDialog(
                self,
                title=title,
                subtitle=tr(
                    "settings.model_dialog.face_subtitle"
                    if feature == "face"
                    else "settings.model_dialog.ocr_subtitle"
                ),
                options=options,
                current_key=current.key,
                on_open_folder=lambda _key: self._open_models_folder(feature),
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            key = dialog.selected_key()
            if self._settings is not None:
                self._settings.set(registry.SETTINGS_KEY, key)
            self._apply_selection(feature, key)
            self._refresh_pack_rows()

        action.triggered.connect(_run)
        return action

    def _pack_details(self, feature: str, spec) -> str:
        if feature == "face":
            return tr(
                "settings.model_dialog.face_details",
                name=spec.title,
                mb=spec.download_mb,
                device=onnx_device(),
                quality=tr(f"search.model.quality.{spec.quality}"),
                speed=tr(f"search.model.speed.{_SPEED_KEYS[spec.speed]}"),
                state=(
                    tr("search.model.installed")
                    if face_models.is_installed(spec)
                    else tr("search.model.missing", mb=spec.download_mb)
                ),
            )
        return tr(
            "settings.model_dialog.ocr_details",
            name=spec.title,
            mb=int(round(spec.total_mb)),
            device=onnx_device(),
            state=(
                tr("search.model.installed")
                if ocr_models.is_installed(spec)
                else tr("search.model.missing", mb=int(round(spec.total_mb)))
            ),
        )

    def _open_models_folder(self, feature: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        registry = self._packs_registry(feature)
        root = registry.models_root()
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def _ai_toggle(self, key: str, default: bool) -> QCheckBox:
        """A switch bound to one of the local-AI feature switches."""

        box = _SettingsSwitch(self)
        checked = (
            bool(self._settings.get(key, default))
            if self._settings is not None
            else default
        )
        box.setChecked(checked)
        box.sync_offset()
        box.toggled.connect(lambda value: self._set_ai_switch(key, value))
        return box

    def _set_ai_switch(self, key: str, value: bool) -> None:
        if self._settings is None:
            return
        self._settings.set(key, bool(value))
        self._refresh_ai_rows()

    def _ai_state_value(self, feature: str) -> _AsyncValue:
        label = _AsyncValue(self)
        if not hasattr(self, "_ai_state_labels"):
            self._ai_state_labels: dict[str, _AsyncValue] = {}
        self._ai_state_labels[feature] = label
        return label

    def _refresh_ai_rows(self) -> None:
        self._schedule_ai_probe()

    def _apply_ai_values(self, values: dict) -> None:
        root = self._library_root()
        people = values.get("people")
        if people is not None and root is not None:
            self._people_cache = (str(root), time.monotonic(), int(people))
        for feature, label in getattr(self, "_ai_state_labels", {}).items():
            label.set_value(self._ai_state_text(feature, values))

    def _ai_state_text(self, feature: str, values: dict) -> str:
        root = self._library_root()
        if root is None:
            return tr("settings.ai_state.no_library")
        pair = values.get(feature)
        if not pair:
            return tr("settings.loading")
        done, total = pair
        if total <= 0:
            return tr("settings.ai_state.no_library")
        text = tr("settings.ai_state.coverage", done=done, total=total)
        if feature == "face":
            people = values.get("people")
            if people is None:
                cache = getattr(self, "_people_cache", None)
                people = cache[2] if cache is not None and cache[0] == str(root) else 0
            if people:
                text = f"{text} · {tr('settings.ai_state.people', count=people)}"
        return text

    # ------------------------------------------------------------------
    # Off-thread value probes
    # ------------------------------------------------------------------
    def _active_key(self, feature: str):
        selection = self._model_selection
        getter = getattr(selection, "key", None)
        if callable(getter):
            return getter(feature)
        registry = (
            search_models
            if feature == "search"
            else self._packs_registry(feature)
        )
        return (
            self._settings.get(registry.SETTINGS_KEY, None)
            if self._settings is not None
            else None
        )

    def _probe_keys(self) -> dict:
        return {feature: self._active_key(feature) for feature in ("search", "face", "ocr")}

    def _status_snapshot(self) -> dict:
        status = self._search_status
        return {
            "device": str(getattr(status, "device", "") or ""),
            "running": bool(getattr(status, "running", False)),
            "done": int(getattr(status, "done", 0) or 0),
            "total": int(getattr(status, "total", 0) or 0),
        }

    def _people_cache_is_fresh(self, root) -> bool:
        cache = getattr(self, "_people_cache", None)
        return bool(
            cache is not None
            and cache[0] == str(root)
            and time.monotonic() - cache[1] < 5.0
        )

    def _schedule_meta_probe(self) -> None:
        if self._closed:
            return
        if self._meta_worker is not None:
            self._meta_pending = True
            return
        worker = _MetaProbe(
            self._library_root(),
            self._probe_keys(),
            self._status_snapshot(),
        )
        self._meta_worker = worker
        _ACTIVE_PROBES.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _schedule_ai_probe(self) -> None:
        if self._closed:
            return
        if self._ai_worker is not None:
            self._ai_pending = True
            return
        root = self._library_root()
        include_people = root is not None and not self._people_cache_is_fresh(root)
        worker = _AiProbe(root, include_people=include_people)
        self._ai_worker = worker
        _ACTIVE_PROBES.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_meta_ready(self, worker, values: dict) -> None:
        if worker is not self._meta_worker:
            return
        self._meta_worker = None
        if self._closed:
            return
        self._meta_values = dict(values)
        self._apply_meta_values(self._meta_values)
        if self._meta_pending:
            self._meta_pending = False
            self._schedule_meta_probe()

    def _on_ai_ready(self, worker, values: dict) -> None:
        if worker is not self._ai_worker:
            return
        self._ai_worker = None
        if self._closed:
            return
        self._ai_values = dict(values)
        self._apply_ai_values(self._ai_values)
        if self._ai_pending:
            self._ai_pending = False
            self._schedule_ai_probe()

    def _on_meta_failed(self, worker, message: str) -> None:  # noqa: ARG002
        if worker is not self._meta_worker:
            return
        self._meta_worker = None
        if self._closed:
            return
        for label in (
            getattr(self, "_model_value_label", None),
            getattr(self, "_index_state_label", None),
            *getattr(self, "_pack_labels", {}).values(),
        ):
            if label is not None:
                label.set_value("")
        if self._meta_pending:
            self._meta_pending = False

    def _on_ai_failed(self, worker, message: str) -> None:  # noqa: ARG002
        if worker is not self._ai_worker:
            return
        self._ai_worker = None
        if self._closed:
            return
        self._ai_values = None
        for label in getattr(self, "_ai_state_labels", {}).values():
            label.set_value("")
        if self._ai_pending:
            self._ai_pending = False

    def _library_root(self):
        library = getattr(self, "_library", None)
        getter = getattr(library, "root", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # pragma: no cover - defensive
            return None

    def _open_ocr_results_action(self) -> QAction:
        action = QAction(tr("settings.row.ocr_results"), self)
        action.triggered.connect(self._show_ocr_results)
        return action

    def _show_ocr_results(self) -> None:
        page = self._ocr_results_page
        if page is None:
            from .ocr_results_page import OcrResultsPage

            page = OcrResultsPage(self._library_root(), self._stack)
            page.backRequested.connect(self._close_ocr_results)
            self._stack.addWidget(page)
            self._ocr_results_page = page
        self._stack.setCurrentWidget(page)
        page.reload()

    def _close_ocr_results(self) -> None:
        if len(self._pages) > 4:
            self._stack.setCurrentWidget(self._pages[4])
            self._nav.setCurrentRow(4)

    def _ai_rebuild_action(self, feature: str) -> QAction:
        """Ask the coordinator to re-run the face or text scan."""

        title = (
            tr("settings.row.face_rebuild")
            if feature == "face"
            else tr("settings.row.ocr_rebuild")
        )
        action = QAction(title, self)

        def _run() -> None:
            from . import dialogs

            if not dialogs.confirm_action(
                self,
                tr(f"settings.rebuild_confirm.{feature}"),
                title=title,
                yes_label=tr("dialog.ok"),
            ):
                return
            if self._settings is None:  # pragma: no cover - settings always exist
                return
            import time

            key = (
                ai_features.FACE_REBUILD_KEY
                if feature == "face"
                else ai_features.OCR_REBUILD_KEY
            )
            self._settings.set(key, time.time())

        action.triggered.connect(_run)
        return action

    def _search_model_value(self) -> _AsyncValue:
        """Show the active search model plus the device that will run it."""

        label = _AsyncValue(self)
        self._model_value_label = label
        return label

    def _switch_model_action(self) -> QAction:
        action = QAction(tr("search.model.switch"), self)

        def _run() -> None:
            dialog = SearchModelDialog(self, current_key=self._active_spec("search").key)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            key = dialog.selected_key()
            if key == self._active_spec("search").key:
                return
            if self._settings is not None:
                self._settings.set(search_models.SETTINGS_KEY, key)
                self._apply_selection("search", key)
                self._refresh_search_rows()
            else:  # pragma: no cover - settings are always present in the app
                self._apply_selection("search", key)

        action.triggered.connect(_run)
        return action

    def _rebuild_index_action(self) -> QAction:
        action = QAction(tr("search.model.rebuild"), self)

        def _run() -> None:
            from . import dialogs

            if not dialogs.confirm_action(
                self,
                tr("search.model.rebuild_confirm"),
                title=tr("search.model.rebuild"),
                yes_label=tr("dialog.ok"),
            ):
                return
            if self._settings is None:  # pragma: no cover - settings always exist
                return
            import time

            from ....agent.services import search_models

            self._settings.set(search_models.REBUILD_KEY, time.time())

        action.triggered.connect(_run)
        return action

    def _close_action_combo(self) -> QComboBox:
        """Let the remembered close action be changed or cleared.

        Without this the "do not ask again" checkbox would be a one-way switch:
        once ticked, nothing in the UI could bring the prompt back.
        """

        combo = QComboBox(self)
        combo.addItem(tr("settings.close.ask_every_time"))
        combo.addItem(tr("settings.close.minimise"))
        combo.addItem(tr("settings.close.quit"))
        current = None
        if self._settings is not None:
            current = self._settings.get("ui.close_action", None)
        combo.setCurrentIndex({None: 0, "minimise": 1, "quit": 2}.get(current, 0))

        def _store(index: int) -> None:
            if self._settings is None:
                return
            value = {0: None, 1: "minimise", 2: "quit"}[index]
            self._settings.set("ui.close_action", value)

        combo.currentIndexChanged.connect(_store)
        return combo

    def _path_label(self, root) -> QLabel:
        label = QLabel(str(root) if root is not None else tr("settings.not_bound"), self)
        label.setObjectName("settingsPathLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setMaximumWidth(420)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    # ------------------------------------------------------------------
    # Appearance
    # ------------------------------------------------------------------
    def _stylesheet(self) -> str:
        palette = self.palette()
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        if dark:
            shell, body_bg, nav_bg = "#1C1C1E", "#1C1C1E", "#171717"
            card_bg = "#232325"
            text, muted = "#F5F5F7", "#B4B4B4"
            divider = "rgba(255, 255, 255, 26)"
            field_bg = "#2C2C2E"
            popup_bg = "#2C2C2E"
            popup_selection = "rgba(255, 255, 255, 30)"
            hover = "rgba(255, 255, 255, 24)"
        else:
            shell, body_bg, nav_bg = "#FFFFFF", "#FFFFFF", "#F9F9F9"
            card_bg = "#F7F7F8"
            text, muted = "#0D0D0D", "#6E6E80"
            divider = "rgba(0, 0, 0, 0.08)"
            field_bg = "#F2F2F4"
            popup_bg = "#FFFFFF"
            popup_selection = "#ECECEC"
            hover = "rgba(0, 0, 0, 0.06)"

        accent = _ACCENT_DARK if dark else _ACCENT_LIGHT
        if hasattr(self, "_shadow_effect"):
            self._shadow_effect.setColor(QColor(0, 0, 0, 104 if dark else 60))

        return (
            f"QDialog#settingsDialog {{ background: transparent; }}"
            "QWidget#settingsCardHost { background: transparent; }"
            f"QWidget#settingsShadowPanel {{ background-color: {shell};"
            "  border-radius: 12px; }"
            f"QWidget#settingsShell {{ background-color: {shell}; border-radius: 12px;"
            f"  border: 1px solid {divider}; }}"
            f"QWidget#settingsBody {{ background-color: {body_bg};"
            "  border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }"
            "QWidget#settingsTitleBar { background: transparent; border: none; }"
            f"QToolButton#settingsCloseButton {{ border: none; border-radius: 8px;"
            "  background: transparent; }"
            f"QToolButton#settingsCloseButton:hover {{ background-color: {hover}; }}"
            f"QToolButton#settingsCloseButton:focus {{ border: 1px solid {accent}; }}"
            f"QListWidget#settingsNav {{ background-color: {nav_bg}; border: none;"
            f"  border-right: 1px solid {divider}; border-bottom-left-radius: 12px;"
            "  padding: 24px 12px; outline: 0; }"
            f"QWidget#settingsPage {{ background-color: {body_bg}; }}"
            "QWidget#settingsPageColumn, QWidget#settingsPageContent { background: transparent; }"
            f"QWidget#ocrResultsPage, QWidget#ocrResultsBody {{ background-color: {body_bg}; }}"
            f"QWidget#ocrResultsHeader {{ background-color: {body_bg};"
            f"  border-bottom: 1px solid {divider}; }}"
            f"QToolButton#settingsBackButton {{ border: none; border-radius: 8px;"
            "  background: transparent; }"
            f"QToolButton#settingsBackButton:hover {{ background-color: {hover}; }}"
            f"QLabel#settingsPageTitle {{ font-size: 20px; font-weight: 600; color: {text}; }}"
            f"QLabel#settingsRowLabel {{ font-size: 14px; color: {text}; }}"
            f"QLabel#settingsPathLabel {{ font-size: 13px; color: {muted}; }}"
            f"QLabel#settingsSectionLabel {{ font-size: 12px; font-weight: 600;"
            f"  color: {muted}; padding-left: 4px; padding-bottom: 2px; }}"
            "QScrollArea#settingsScroll { background: transparent; }"
            f"QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {divider}; border-radius: 3px;"
            "  min-height: 32px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            f"QWidget#settingsCard {{ background-color: {card_bg}; border-radius: 12px;"
            f"  border: 1px solid {divider}; }}"
            f"QWidget#settingsDivider {{ background-color: {divider}; }}"
            f"QWidget#settingsActionRow:hover {{ background-color: {hover};"
            "  border-radius: 10px; }"
            f"QComboBox {{ min-height: 30px; min-width: 180px; padding: 0 10px;"
            f"  border: 1px solid {divider}; border-radius: 8px; font-size: 13px;"
            f"  background-color: {field_bg}; color: {text}; }}"
            f"QComboBox:hover {{ background-color: {hover}; }}"
            f"QComboBox:focus {{ border: 1px solid {accent}; }}"
            # The popup is its own top-level view: without an explicit background
            # and text colour it renders as a black plate with unreadable labels.
            f"QComboBox QAbstractItemView {{ background-color: {popup_bg}; color: {text};"
            f"  border: 1px solid {divider}; border-radius: 8px; padding: 4px;"
            "  outline: 0; }"
            f"QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px;"
            "  border-radius: 6px; }"
            f"QComboBox QAbstractItemView::item:selected {{ background-color: {popup_selection};"
            f"  color: {text}; }}"
        )

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def _on_nav_changed(self, row: int) -> None:
        """Start the expensive AI probes only once 智能 is actually opened."""

        if row == _AI_PAGE_INDEX:
            first_visit = not self._ai_probes_started
            self._ensure_ai_probes()
            if not first_visit:
                self._refresh_search_rows()
                self._refresh_ai_rows()
            if not self._ai_refresh_timer.isActive():
                self._ai_refresh_timer.start()
            return
        self._ai_refresh_timer.stop()

    def _ensure_ai_probes(self) -> None:
        if self._ai_probes_started:
            return
        self._ai_probes_started = True
        self._schedule_meta_probe()
        self._schedule_ai_probe()

    def _on_page_scrolled(self, _value: int) -> None:
        """Suspend the expensive card shadow while a page is scrolling."""

        if not self._closing and self._shadow_effect.isEnabled():
            self._shadow_effect.setEnabled(False)
        self._shadow_idle_timer.start()

    def _restore_shadow(self) -> None:
        if self._closing:
            return
        if not self._shadow_effect.isEnabled():
            self._shadow_effect.setEnabled(True)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt style
        # The transparent shadow ring belongs to this window; treat it like the
        # dark scrim and dismiss on click.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and hasattr(self, "_shell")
            and not self._shell.geometry().contains(event.position().toPoint())
        ):
            self.close()
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt style
        super().resizeEvent(event)
        self._sync_card_geometry()

    def eventFilter(self, obj: QObject, event) -> bool:  # noqa: N802 - Qt style
        if obj is self._scrim_parent:
            if event.type() == QEvent.Type.Resize and self._scrim is not None:
                try:
                    self._scrim.setGeometry(self._scrim_parent.rect())
                except RuntimeError:
                    # The main window is being torn down with the dialog open.
                    self._scrim = None
            elif (
                event.type() == QEvent.Type.WindowActivate
                and self.isVisible()
                and not self._opening
                and not self._closing
            ):
                # The dim layer covers the client area but not the OS title bar;
                # activating the main window from there should dismiss settings.
                QTimer.singleShot(0, self.close)
        return super().eventFilter(obj, event)

    def _sync_card_geometry(self) -> None:
        """Place the card and its keyboard focus ring inside the shadow margin."""

        if not hasattr(self, "_shell"):
            return
        rect = self.rect().adjusted(
            _SHADOW_MARGIN,
            _SHADOW_MARGIN,
            -_SHADOW_MARGIN,
            -_SHADOW_MARGIN,
        )
        if rect.width() <= 0 or rect.height() <= 0:
            return
        self._shadow_panel.setGeometry(rect)
        self._shell.setGeometry(rect)
        self.close_button.move(rect.width() - 16 - self.close_button.width(), 16)
        self.close_button.raise_()

    def _center_on_parent(self) -> None:
        """Keep the whole window smaller than the main window, then centre it."""

        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else self.screen()
        available = screen.availableGeometry() if screen is not None else None

        # The window includes the transparent ring reserved for the shadow, so
        # leave a 20 px gap on every side of the main window.
        max_window_w = _CARD_WIDTH + 2 * _SHADOW_MARGIN
        max_window_h = _CARD_HEIGHT + 2 * _SHADOW_MARGIN
        if parent is not None:
            max_window_w = min(max_window_w, max(480, parent.width() - 40))
            max_window_h = min(max_window_h, max(400, parent.height() - 40))
        if available is not None:
            max_window_w = min(max_window_w, max(480, available.width() - 40))
            max_window_h = min(max_window_h, max(400, available.height() - 40))

        card_w = max(560, min(_CARD_WIDTH, max_window_w - 2 * _SHADOW_MARGIN))
        card_h = max(420, min(_CARD_HEIGHT, max_window_h - 2 * _SHADOW_MARGIN))
        self.setMinimumSize(
            min(_CARD_MIN_WIDTH, card_w) + 2 * _SHADOW_MARGIN,
            min(_CARD_MIN_HEIGHT, card_h) + 2 * _SHADOW_MARGIN,
        )
        self.resize(card_w + 2 * _SHADOW_MARGIN, card_h + 2 * _SHADOW_MARGIN)

        if parent is not None:
            frame = parent.frameGeometry()
            x = frame.center().x() - self.width() // 2
            y = frame.center().y() - self.height() // 2
        elif available is not None:
            x = available.center().x() - self.width() // 2
            y = available.center().y() - self.height() // 2
        else:
            return

        if available is not None:
            x = min(max(x, available.left() + 20), available.right() - self.width() - 20)
            y = min(max(y, available.top() + 20), available.bottom() - self.height() - 20)
        self.move(x, y)

    def _ensure_scrim(self) -> None:
        parent = self._scrim_parent
        if parent is None or not parent.isVisible():
            return
        if self._scrim is None:
            self._scrim = _SettingsScrim(parent)
            self._scrim.clicked.connect(self.close)
            parent.installEventFilter(self)
        self._scrim.setGeometry(parent.rect())
        self._scrim.fade_in(animate=_ANIMATIONS)

    def _remove_scrim(self) -> None:
        scrim = self._scrim
        self._scrim = None
        if self._scrim_parent is not None:
            try:
                self._scrim_parent.removeEventFilter(self)
            except RuntimeError:
                pass
        if scrim is not None:
            try:
                scrim.hide()
                scrim.deleteLater()
            except RuntimeError:
                pass

    def _end_open_guard(self) -> None:
        """Never leave the card invisible if a platform stalls the fade."""

        self._opening = False
        self._opening_guard.stop()
        if self._open_anim is not None:
            self._open_anim.stop()
        self.setWindowOpacity(1.0)
        if self._open_target is not None:
            self.move(self._open_target)

    def _start_open_animation(self) -> None:
        if self._open_anim is not None:
            self._open_anim.stop()
        target = self.pos()
        self._open_target = target
        self.move(target.x(), target.y() + 8)

        group = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(180)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self, b"pos", group)
        slide.setDuration(180)
        slide.setStartValue(QPoint(target.x(), target.y() + 8))
        slide.setEndValue(target)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _done() -> None:
            self.setWindowOpacity(1.0)
            self.move(target)
            self._opening = False

        group.finished.connect(_done)
        group.start()
        self._open_anim = group

    def _start_close_animation(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._opening_guard.stop()
        if self._open_anim is not None:
            self._open_anim.stop()

        if self._scrim is not None:
            self._scrim.fade_out(animate=_ANIMATIONS)

        if not _ANIMATIONS:
            self._finish_close()
            return

        target = self.pos()
        group = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(160)
        opacity.setStartValue(self.windowOpacity())
        opacity.setEndValue(0.0)
        opacity.setEasingCurve(QEasingCurve.Type.InCubic)

        slide = QPropertyAnimation(self, b"pos", group)
        slide.setDuration(160)
        slide.setStartValue(target)
        slide.setEndValue(QPoint(target.x(), target.y() + 6))
        slide.setEasingCurve(QEasingCurve.Type.InCubic)

        group.finished.connect(self._finish_close)
        group.start()
        self._close_anim = group
        # If the platform stalls the opacity/pos animation, still close.
        self._close_guard.start(300)

    def _finish_close(self) -> None:
        self._close_guard.stop()
        self._opening_guard.stop()
        if self._open_anim is not None:
            self._open_anim.stop()
        self._allow_close = True
        self._remove_scrim()
        self.close()

    def showEvent(self, event) -> None:  # noqa: N802
        self._opening = True
        self._center_on_parent()
        self._sync_card_geometry()
        self._shadow_idle_timer.stop()
        self._restore_shadow()
        if _ANIMATIONS:
            self.setWindowOpacity(0.0)
        super().showEvent(event)
        self._ensure_scrim()
        # The AI probes are deferred until 智能 is opened; if the dialog was
        # hidden while that page was selected, refresh it again now.
        self._on_nav_changed(self._nav.currentRow())
        if _ANIMATIONS:
            self._opening_guard.start(400)
            self._start_open_animation()
        else:
            self._opening = False

    def hideEvent(self, event) -> None:  # noqa: N802
        self._ai_refresh_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closed = True
        self._ai_refresh_timer.stop()

        if (
            self._allow_close
            or not self.isVisible()
            or not _ANIMATIONS
            or QCoreApplication.closingDown()
        ):
            # Connections are owned by this QObject and disappear with it. Do
            # not disconnect manually: PySide emits a RuntimeWarning when a
            # worker has already finished and the connection no longer exists.
            page = getattr(self, "_ocr_results_page", None)
            if page is not None:
                page.shutdown()
            self._remove_scrim()
            super().closeEvent(event)
            return

        event.ignore()
        self._start_close_animation()


__all__ = ["SettingsDialog"]
