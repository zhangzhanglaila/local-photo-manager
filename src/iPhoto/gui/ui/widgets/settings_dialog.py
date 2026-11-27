"""Standalone settings window.

The toolbar used to hide every application option behind a "..." menu. Settings
now opens the same options in a dedicated window, drawn like the reference
application: a rounded frameless card, a title row with its own close button, a
navigation column on the left and one settings page on the right.
"""

from __future__ import annotations

import re
import sqlite3
import time

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QPoint,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
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


class _AsyncValue(QWidget):
    """A value row that spins until its background probe returns."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.spinner = Spinner(14, self)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel(tr("preview.loading"), self)
        self.label.setObjectName("settingsPathLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label.setWordWrap(False)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.spinner.start()

    def set_value(self, text: str) -> None:
        self.label.setText(text)
        self.spinner.stop()
        self.spinner.hide()

    def set_loading(self) -> None:
        self.label.setText(tr("preview.loading"))
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


class _SettingsNavDelegate(QStyledItemDelegate):
    """Rounded navigation rows with the accent bar used by the sidebar."""

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
            painter.setBrush(pill if selected else QColor(pill.red(), pill.green(), pill.blue(), 130))
            painter.drawRoundedRect(QRectF(rect).adjusted(0, 1, 0, -1), 10, 10)

        if selected:
            accent = QColor(_ACCENT_DARK if dark else _ACCENT_LIGHT)
            painter.setBrush(accent)
            painter.setPen(Qt.PenStyle.NoPen)
            bar = QRectF(rect.left(), rect.center().y() - 8, 3, 16)
            painter.drawRoundedRect(bar, 1.5, 1.5)

        font = QFont(option.font)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        colour = QColor(option.palette.color(QPalette.ColorRole.Text))
        if not selected:
            colour.setAlpha(170)
        painter.setPen(colour)
        text_rect = rect.adjusted(16, 0, -8, 0)
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
    """Frameless title row: title on the left, close button on the right."""

    def __init__(self, dialog: "SettingsDialog") -> None:
        super().__init__(dialog)
        self.setObjectName("settingsTitleBar")
        self.setFixedHeight(56)
        self._dialog = dialog
        self._press_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 12, 0)
        layout.setSpacing(8)

        self.title_label = QLabel(tr("dialog.settings_title"), self)
        self.title_label.setObjectName("settingsTitleLabel")
        layout.addWidget(self.title_label, 1)

        self.close_button = QToolButton(self)
        self.close_button.setObjectName("settingsCloseButton")
        glyph = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        glyph.setAlpha(160)
        self.close_button.setIcon(
            load_icon(
                "window.close.svg",
                color=glyph.name(QColor.NameFormat.HexArgb),
                size=(14, 14),
            )
        )
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setFixedSize(32, 32)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Close")
        self.close_button.clicked.connect(dialog.close)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)

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
        self.resize(900, 620)
        self.setMinimumSize(760, 520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._shell = QWidget(self)
        self._shell.setObjectName("settingsShell")
        self._shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self._shell)

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
        self._nav.setFixedWidth(210)
        self._nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._nav.setItemDelegate(_SettingsNavDelegate(self._nav))
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setMouseTracking(True)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_layout.addWidget(self._nav)

        self._stack = QStackedWidget(body)
        self._stack.setObjectName("settingsStack")
        body_layout.addWidget(self._stack, 1)
        shell_layout.addWidget(body, 1)

        self._build_pages()
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        # Clicking the already-selected 智能 row while the OCR browser is open
        # does not emit currentRowChanged; itemClicked still returns to settings.
        self._nav.itemClicked.connect(
            lambda item: self._stack.setCurrentIndex(self._nav.row(item))
        )
        self._nav.setCurrentRow(0)
        self.setStyleSheet(self._stylesheet())
        # Values are populated asynchronously: constructing this window used to
        # import onnxruntime/torch and open three SQLite databases on the GUI
        # thread, so simply opening 设置 froze the whole application.
        self._schedule_meta_probe()
        self._schedule_ai_probe()

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
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(6)

        heading = QLabel(title, content)
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
        box = QCheckBox(self)
        box.setChecked(action.isChecked())
        box.toggled.connect(action.setChecked)
        action.toggled.connect(box.setChecked)
        return box

    def _combo(self, actions) -> QComboBox:
        actions = list(actions)
        combo = QComboBox(self)
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
        """A checkbox bound to one of the local-AI switches."""

        box = QCheckBox(self)
        checked = (
            bool(self._settings.get(key, default))
            if self._settings is not None
            else default
        )
        box.setChecked(checked)
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
            return tr("preview.loading")
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

        return (
            f"QDialog#settingsDialog {{ background: transparent; }}"
            f"QWidget#settingsShell {{ background-color: {shell}; border-radius: 12px;"
            f"  border: 1px solid {divider}; }}"
            f"QWidget#settingsBody {{ background-color: {body_bg};"
            "  border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }"
            f"QWidget#settingsTitleBar {{ border-bottom: 1px solid {divider}; }}"
            f"QLabel#settingsTitleLabel {{ font-size: 15px; font-weight: 600; color: {text}; }}"
            f"QToolButton#settingsCloseButton {{ border: none; border-radius: 8px;"
            "  background: transparent; }"
            f"QToolButton#settingsCloseButton:hover {{ background-color: {hover}; }}"
            f"QListWidget#settingsNav {{ background-color: {nav_bg}; border: none;"
            f"  border-right: 1px solid {divider}; border-bottom-left-radius: 12px;"
            "  padding: 18px 12px; outline: 0; }"
            f"QWidget#settingsPage {{ background-color: {body_bg}; }}"
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
            "QScrollArea#settingsScroll, QWidget#settingsPageContent { background: transparent; }"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {divider}; border-radius: 5px;"
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
            # The popup is its own top-level view: without an explicit background
            # and text colour it renders as a black plate with unreadable labels.
            f"QComboBox QAbstractItemView {{ background-color: {popup_bg}; color: {text};"
            f"  border: 1px solid {divider}; border-radius: 8px; padding: 4px;"
            "  outline: 0; }"
            f"QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px;"
            "  border-radius: 6px; }"
            f"QComboBox QAbstractItemView::item:selected {{ background-color: {popup_selection};"
            f"  color: {text}; }}"
            f"QCheckBox {{ color: {text}; }}"
            "QCheckBox::indicator { width: 18px; height: 18px; }"
        )

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Values are already probed in the background from __init__; refresh once
        # more in case the dialog was hidden while a scan progressed.
        self._refresh_search_rows()
        self._refresh_ai_rows()
        self._ai_refresh_timer.start()
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )

    def hideEvent(self, event) -> None:  # noqa: N802
        self._ai_refresh_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closed = True
        self._ai_refresh_timer.stop()
        # Connections are owned by this QObject and disappear with it. Do not
        # disconnect manually: PySide emits a RuntimeWarning when a worker has
        # already finished and the connection no longer exists.
        page = getattr(self, "_ocr_results_page", None)
        if page is not None:
            page.shutdown()
        super().closeEvent(event)


__all__ = ["SettingsDialog"]
