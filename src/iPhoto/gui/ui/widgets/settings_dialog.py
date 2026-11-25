"""Standalone settings window.

The toolbar used to hide every application option behind a "..." menu. Settings
now opens the same options in a dedicated window, drawn like the reference
application: a rounded frameless card, a title row with its own close button, a
navigation column on the left and one settings page on the right.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
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
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon

_ACCELERATOR_RE = re.compile(r"\s*\(&[A-Za-z0-9]\)\s*$")

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
        self, header, library=None, parent: QWidget | None = None, settings=None
    ) -> None:
        super().__init__(parent)
        self._header = header
        self._library = library
        self._settings = settings
        self._pages: list[QWidget] = []

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

        page = QWidget(self)
        page.setObjectName("settingsPage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(6)

        heading = QLabel(title, page)
        heading.setObjectName("settingsPageTitle")
        layout.addWidget(heading)
        layout.addSpacing(12)

        for section_title, rows in sections:
            if section_title:
                label = QLabel(section_title, page)
                label.setObjectName("settingsSectionLabel")
                layout.addWidget(label)
            layout.addWidget(self._card(rows, page))
            layout.addSpacing(14)
        layout.addStretch(1)
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
        parent = self.parentWidget()
        if parent is not None:
            frame = parent.frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )


__all__ = ["SettingsDialog"]
