"""Standalone settings window.

The toolbar used to hide every application option behind a "..." menu. Settings
now opens the same options in a dedicated window, drawn like the reference
application: a rounded frameless card, a title row with its own close button, a
navigation column on the left and one settings page on the right.
"""

from __future__ import annotations

import re
import time

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
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
from .search_model_dialog import (
    SearchModelDialog,
    current_device_label,
    model_title,
)

_ACCELERATOR_RE = re.compile(r"\s*\(&[A-Za-z0-9]\)\s*$")

#: ``FaceModelSpec.speed`` is a 1-3 rank; the translations are named.
_SPEED_KEYS = {1: "medium", 2: "fast", 3: "fastest"}

_ACCENT_LIGHT = "#7B5E3B"
_ACCENT_DARK = "#C9A86C"


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
        #: Coverage is read from the AI databases, which change underneath the
        #: dialog while a face/OCR scan runs. A light poll keeps "processed /
        #: total" moving; there is no observable signal for those counters.
        self._ai_refresh_timer = QTimer(self)
        self._ai_refresh_timer.setInterval(1000)
        self._ai_refresh_timer.timeout.connect(self._refresh_ai_rows)
        self._people_cache: tuple[str, float, int] | None = None

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
        self._nav.setCurrentRow(0)
        self.setStyleSheet(self._stylesheet())

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

    def _index_state_value(self) -> QLabel:
        """Live coverage / rebuild progress for the active model."""

        label = QLabel("", self)
        label.setObjectName("settingsPathLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._index_state_label = label
        self._refresh_search_rows()
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
        """Keep the model and index rows honest after a switch or a progress tick."""

        label = getattr(self, "_model_value_label", None)
        state_label = getattr(self, "_index_state_label", None)
        spec = self._active_spec("search")
        state = (
            ""
            if search_models.is_installed(spec)
            else tr("search.model.not_installed_suffix")
        )
        if label is not None:
            label.setText(
                f"{model_title(spec)}{state} · {current_device_label(self._search_status)}"
            )

        if state_label is None:
            return
        status = self._search_status
        running = bool(status.running) if status is not None else False
        done = status.done if status is not None else 0
        total = status.total if status is not None else 0
        if not running and total == 0:
            # Nothing has indexed since startup, so ask the database directly:
            # showing "not indexed yet" while 10k vectors exist is misleading.
            coverage = self._compute_coverage()
            if coverage is not None:
                done, total = coverage
        if running and total:
            percent = int(done * 100 / total)
            state_label.setText(
                tr(
                    "search.model.indexing_now",
                    percent=percent,
                    done=done,
                    total=total,
                )
            )
        elif total:
            state_label.setText(tr("search.model.coverage", done=done, total=total))
        else:
            state_label.setText(tr("search.model.coverage_unknown"))

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

    def _packs_value(self, feature: str) -> QLabel:
        """Show the selected pack as a value row, like 搜索模型 does."""

        label = QLabel("", self)
        label.setObjectName("settingsPathLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setWordWrap(False)
        if not hasattr(self, "_pack_labels"):
            self._pack_labels: dict[str, QLabel] = {}
        self._pack_labels[feature] = label
        self._refresh_pack_rows()
        return label

    def _refresh_pack_rows(self) -> None:
        for feature, label in getattr(self, "_pack_labels", {}).items():
            registry = self._packs_registry(feature)
            spec = registry.resolve_spec(
                self._settings.get(registry.SETTINGS_KEY, None)
                if self._settings is not None
                else None
            )
            state = (
                ""
                if registry.is_installed(spec)
                else tr("search.model.not_installed_suffix")
            )
            label.setText(f"{spec.title}{state} · {onnx_device()}")

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

    def _ai_state_value(self, feature: str) -> QLabel:
        label = QLabel("", self)
        label.setObjectName("settingsPathLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if not hasattr(self, "_ai_state_labels"):
            self._ai_state_labels: dict[str, QLabel] = {}
        self._ai_state_labels[feature] = label
        self._refresh_ai_rows()
        return label

    def _refresh_ai_rows(self) -> None:
        for feature, label in getattr(self, "_ai_state_labels", {}).items():
            label.setText(self._ai_state_text(feature))

    def _ai_state_text(self, feature: str) -> str:
        root = self._library_root()
        if root is None:
            return tr("settings.ai_state.no_library")
        try:
            from ....agent.services.ai_coverage import (
                face_coverage,
                ocr_coverage,
            )

            if feature == "face":
                done, total = face_coverage(root)
                if total <= 0:
                    return tr("settings.ai_state.no_library")
                text = tr("settings.ai_state.coverage", done=done, total=total)
                people = self._cached_people_count(root)
                if people:
                    text = f"{text} · {tr('settings.ai_state.people', count=people)}"
                return text
            done, total = ocr_coverage(root)
            if total <= 0:
                return tr("settings.ai_state.no_library")
            return tr("settings.ai_state.coverage", done=done, total=total)
        except Exception:  # pragma: no cover - display only
            return ""

    def _cached_people_count(self, root) -> int:
        """Count people at most every few seconds.

        The face index is a separate database; opening it on every one-second
        coverage tick made the scan and the dialog compete for SQLite.
        """

        now = time.monotonic()
        key = str(root)
        cache = getattr(self, "_people_cache", None)
        if cache is not None and cache[0] == key and now - cache[1] < 5.0:
            return cache[2]
        from ....agent.services.ai_coverage import people_count

        count = people_count(root)
        self._people_cache = (key, now, count)
        return count

    def _library_root(self):
        library = getattr(self, "_library", None)
        getter = getattr(library, "root", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # pragma: no cover - defensive
            return None

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

    def _compute_coverage(self) -> tuple[int, int] | None:
        """Return ``(indexed, photos)`` for the active model, if a library is open."""

        library = getattr(self, "_library", None)
        getter = getattr(library, "root", None)
        root = None
        if callable(getter):
            try:
                root = getter()
            except Exception:  # pragma: no cover - defensive
                root = None
        if root is None:
            return None
        try:
            from ....agent.services.search_coverage import compute_coverage

            return compute_coverage(root, self._active_spec("search").dir_name)
        except Exception:  # pragma: no cover - display only
            return None

    def _search_model_value(self) -> QLabel:
        """Show the active search model plus the device that will run it."""

        spec = self._active_spec("search")
        # Keep it short: quality/language/size live in the chooser, the row only
        # needs "which model" and "where it runs". A model that is not on disk is
        # called out instead of pretending it is usable.
        state = (
            ""
            if search_models.is_installed(spec)
            else tr("search.model.not_installed_suffix")
        )
        label = QLabel(
            f"{model_title(spec)}{state} · {current_device_label(self._search_status)}",
            self,
        )
        label.setObjectName("settingsPathLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setWordWrap(False)
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
        # Re-read the coverage now, then keep it moving while a scan runs.
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


__all__ = ["SettingsDialog"]
