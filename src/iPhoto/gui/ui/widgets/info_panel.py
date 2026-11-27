"""Floating window that displays EXIF metadata for the selected asset."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ....i18n import tr

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QShowEvent,
)
from ..theme_mode import is_dark
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from iPhoto.people.records import AssetFaceAnnotation, PersonSummary

from ..icons import load_icon
from ...menus.core import MenuActionSpec, MenuContext, populate_menu
from ...menus.style import apply_menu_style
from .info_location_map import InfoLocationMapView
from .info_panel_chrome import InfoPanelChromeMixin
from .info_panel_content import InfoPanelContentMixin
from .info_panel_formatters import _FormattedMetadata, InfoPanelFormattingMixin  # noqa: F401
from .info_panel_location import InfoPanelLocationMixin
from .main_window_metrics import TITLE_BAR_HEIGHT, WINDOW_CONTROL_BUTTON_SIZE, WINDOW_CONTROL_GLYPH_SIZE
from .people_dashboard_dialogs import GroupPeopleDialog

# Matches a lens string that is already a self-contained spec with *both* a
# focal-length component ("23mm", "24-70mm") *and* an aperture component
# ("f/2", "f/3.5").  A bare lens-model name like "XF23mmF2 R WR" or an iPhone
# string like "back camera 4.2mm f/1.6" both qualify, but so does a formatted
# LensInfo spec like "23mm f/2".  When the full pattern matches we skip
# appending the separate focal/aperture EXIF fields to avoid duplication.
# Note: requires an explicit "f/" prefix so that "XF23mmF2" alone does NOT
# trigger the early return — the "F2" token there is not preceded by "f/".
_PLUS_CIRCLE_ICON_PATH = Path(__file__).resolve().parents[1] / "icon" / "plus.circle.svg"
_FACE_AVATAR_DIAMETER = 48
#: Stable menu action ids. Dispatch reads the id, so a translated label can never
#: change behaviour (the labels themselves come from ``_menu_action_labels``).
_NOT_THIS_CHOOSE_ID = "not_this_person:choose"
_NOT_THIS_NEW_ID = "not_this_person:new"


def _parse_svg_dimension(value: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
    return float(match.group(1)) if match is not None else 0.0


def _measure_svg_path_bounds(svg_path: Path) -> tuple[float, float, float, float]:
    try:
        text = svg_path.read_text(encoding="utf-8")
    except OSError:
        return (0.0, 0.0, 0.0, 0.0)
    path_match = re.search(r"d='([^']+)'", text, re.DOTALL)
    if path_match is None:
        return (0.0, 0.0, 0.0, 0.0)
    numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path_match.group(1))]
    if len(numbers) < 2:
        return (0.0, 0.0, 0.0, 0.0)
    xs = numbers[0::2]
    ys = numbers[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def _face_add_button_metrics() -> tuple[QSize, QSize]:
    try:
        text = _PLUS_CIRCLE_ICON_PATH.read_text(encoding="utf-8")
    except OSError:
        fallback = QSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
        return fallback, fallback
    width_match = re.search(r"width='([^']+)'", text)
    height_match = re.search(r"height='([^']+)'", text)
    svg_width = _parse_svg_dimension(width_match.group(1)) if width_match is not None else 0.0
    svg_height = _parse_svg_dimension(height_match.group(1)) if height_match is not None else 0.0
    min_x, min_y, max_x, max_y = _measure_svg_path_bounds(_PLUS_CIRCLE_ICON_PATH)
    outer_width = max(1.0, max_x - min_x)
    outer_height = max(1.0, max_y - min_y)
    if svg_width <= 0.0 or svg_height <= 0.0:
        fallback = QSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
        return fallback, fallback
    scale = max(_FACE_AVATAR_DIAMETER / outer_width, _FACE_AVATAR_DIAMETER / outer_height)
    icon_size = QSize(
        max(_FACE_AVATAR_DIAMETER, int(round(svg_width * scale))),
        max(_FACE_AVATAR_DIAMETER, int(round(svg_height * scale))),
    )
    button_side = max(icon_size.width(), icon_size.height())
    return icon_size, QSize(button_side, button_side)


_FACE_ADD_ICON_SIZE, _FACE_ADD_BUTTON_SIZE = _face_add_button_metrics()


def _uses_dark_theme(widget: QWidget | None) -> bool:
    """Return whether the panel should render dark."""

    return is_dark(widget)


class _FaceAvatarWidget(QLabel):
    deleteRequested = Signal(object)
    moveRequested = Signal(object, str)
    newPersonRequested = Signal(object, str)

    _ACTIVE_BORDER_COLOR = "#0A84FF"
    _PLACEHOLDER_STYLE = (
        f"background-color: rgba(207, 214, 225, 220); border-radius: {_FACE_AVATAR_DIAMETER // 2}px;"
    )

    def __init__(
        self,
        annotation: AssetFaceAnnotation,
        candidates: list[PersonSummary],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._annotation = annotation
        self._candidates = list(candidates)
        self._is_menu_active = False
        self._is_placeholder = False
        self.setFixedSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_visual_state()

    def set_candidates(self, candidates: list[PersonSummary]) -> None:
        self._candidates = list(candidates)

    def contextMenuEvent(self, event) -> None:
        menu = self._build_context_menu()
        if menu is None:
            event.ignore()
            return
        self._set_menu_active(True)
        menu.aboutToHide.connect(lambda: self._set_menu_active(False))
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        self._handle_menu_choice(chosen)

    def _handle_menu_choice(self, chosen: QAction) -> None:
        """Dispatch one chosen menu item by its action id, never by its label."""

        action_id = chosen.data()
        if action_id == "delete_face":
            self.deleteRequested.emit(self._annotation)
            return
        if action_id == _NOT_THIS_CHOOSE_ID:
            self._prompt_choose_person()
            return
        if action_id == _NOT_THIS_NEW_ID:
            self._prompt_new_person()

    def _build_context_menu(self) -> QMenu | None:
        delete_label, not_this_label, submenu_labels = self._menu_action_labels()
        menu = QMenu(self)
        apply_menu_style(menu, self)
        context = MenuContext(
            surface="info_panel",
            selection_kind="empty",
            entity_kind="person",
            entity_id=self._annotation.person_id,
        )
        populate_menu(
            menu,
            context=context,
            action_specs=[
                MenuActionSpec(
                    action_id="delete_face",
                    label=delete_label,
                ),
                MenuActionSpec(
                    action_id="not_this_person",
                    label=not_this_label,
                    children=(
                        MenuActionSpec(
                            action_id=_NOT_THIS_CHOOSE_ID,
                            label=submenu_labels[0],
                        ),
                        MenuActionSpec(
                            action_id=_NOT_THIS_NEW_ID,
                            label=submenu_labels[1],
                        ),
                    ),
                ),
            ],
            anchor=self,
        )
        submenu = menu.actions()[1].menu() if len(menu.actions()) > 1 else None
        if submenu is not None:
            menu._face_action_submenu = submenu  # type: ignore[attr-defined]
        return menu

    def _menu_action_labels(self) -> tuple[str, str, tuple[str, str]]:
        return ("Delete", self._not_this_label(), ("Choose Someone Else…", "New Person…"))

    def _not_this_label(self) -> str:
        display_name = str(self._annotation.display_name or "").strip()
        return f"Not {display_name}" if display_name else "Not This Person"

    def _prompt_choose_person(self) -> None:
        options = [
            summary
            for summary in self._candidates
            if summary.person_id and summary.person_id != self._annotation.person_id
        ]
        if not options:
            return
        host = self.window() if isinstance(self.window(), QWidget) else self
        dialog = GroupPeopleDialog(
            options,
            title_text=tr("people.select_other"),
            prompt_text=tr("people.assign_face_to"),
            confirm_text=tr("people.select"),
            min_selection=1,
            max_selection=1,
            dark_mode=_uses_dark_theme(host),
            parent=host,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_ids = dialog.selected_person_ids()
        if not selected_ids:
            return
        self.moveRequested.emit(self._annotation, selected_ids[0])

    def _prompt_new_person(self) -> None:
        host = self.window() if isinstance(self.window(), QWidget) else self
        from .input_dialog import prompt_text

        text, accepted = prompt_text(
            host, title="New Person", label="Person name:", placeholder="姓名"
        )
        if not accepted:
            return
        new_name = text.strip()
        if not new_name:
            return
        self.newPersonRequested.emit(self._annotation, new_name)

    def _set_menu_active(self, active: bool) -> None:
        self._is_menu_active = bool(active)
        self._refresh_visual_state()

    def _refresh_visual_state(self) -> None:
        pixmap = _avatar_pixmap(self._annotation.thumbnail_path)
        border = (
            f"border: 2px solid {self._ACTIVE_BORDER_COLOR};"
            if self._is_menu_active
            else "border: none;"
        )
        if pixmap is not None:
            self._is_placeholder = False
            self.setPixmap(pixmap)
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setStyleSheet(
                f"QLabel {{ background: transparent; border-radius: {_FACE_AVATAR_DIAMETER // 2}px; {border} }}"
            )
            return
        self._is_placeholder = True
        self.clear()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText(" ")
        self.setStyleSheet(
            f"QLabel {{ {self._PLACEHOLDER_STYLE} {border} }}"
        )


def _person_choice_label(summary: PersonSummary) -> str:
    display_name = str(summary.name or "").strip() or "Unnamed"
    return f"{display_name} ({summary.asset_count}张)"


class InfoPanel(
    InfoPanelChromeMixin,
    InfoPanelContentMixin,
    InfoPanelFormattingMixin,
    InfoPanelLocationMixin,
    QWidget,
):
    """Small helper window that mirrors macOS Photos' info popover.

    The panel uses a frameless rounded window with a custom title bar
    whose close button reuses the main window's ``red.close.circle.svg``
    glyph for visual consistency.
    """

    _CORNER_RADIUS = 12.0
    _SHADOW_SIZE = 16
    _SHADOW_MAX_ALPHA = 18
    _SHADOW_RADIUS_GROWTH = 0.5
    dismissed = Signal()
    manualFaceAddRequested = Signal()
    faceDeleteRequested = Signal(object)
    faceMoveRequested = Signal(object, str)
    faceMoveToNewPersonRequested = Signal(object, str)
    locationQueryChanged = Signal(str)
    locationSuggestionActivated = Signal(object)
    locationConfirmRequested = Signal(str, object)
    downloadMapExtensionRequested = Signal()
    _DRAG_EVENT_TYPES = frozenset(
        (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ),
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(320)

        self._metadata: Optional[dict[str, Any]] = None
        self._current_rel: Optional[str] = None
        self._asset_faces: list[AssetFaceAnnotation] = []
        self._face_action_candidates: list[PersonSummary] = []
        self._drag_active = False
        self._drag_offset = None
        self._centered = False
        self._post_show_reflow_queued = False
        self._post_show_reflow_recenter = False
        self._location_capability_enabled = False
        self._location_preview_enabled = False
        self._location_fallback_text = "Install the map extension to use Assign a Location."
        self._location_suggestions: list[object] = []
        self._selected_location_suggestion: object | None = None
        self._updating_location_ui = False
        self._location_dirty = False
        self._location_confirm_queued = False
        self._last_location_map_target: tuple[float, float] | None = None

        # -- title bar -----------------------------------------------------
        self._title_bar = QWidget(self)
        self._title_bar.setFixedHeight(TITLE_BAR_HEIGHT)
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(16, 10, 12, 6)
        title_layout.setSpacing(8)

        self._title_label = QLabel("Info", self._title_bar)
        self._title_label.setObjectName("infoPanelTitleLabel")
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        )
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self._title_label.installEventFilter(self)
        title_layout.addWidget(self._title_label, 1)

        self._close_button = QToolButton(self._title_bar)
        self._close_button.setIcon(load_icon("red.close.circle.svg"))
        self._close_button.setIconSize(WINDOW_CONTROL_GLYPH_SIZE)
        self._close_button.setFixedSize(WINDOW_CONTROL_BUTTON_SIZE)
        self._close_button.setAutoRaise(True)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._close_button.setToolTip("Close")
        self._apply_close_button_style()
        self._close_button.clicked.connect(self.close)
        title_layout.addWidget(
            self._close_button, 0, Qt.AlignmentFlag.AlignRight,
        )
        self._title_bar.installEventFilter(self)

        # -- content labels ------------------------------------------------
        self._filename_label = self._make_content_label()
        self._timestamp_label = self._make_content_label()
        self._camera_label = self._make_content_label()
        self._lens_label = self._make_content_label()
        self._summary_label = self._make_content_label()
        self._exposure_label = self._make_content_label()

        # -- root layout ---------------------------------------------------
        s = self._SHADOW_SIZE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, s, s)
        layout.setSpacing(0)
        layout.addWidget(self._title_bar)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 8, 16, 16)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._filename_label)
        content_layout.addWidget(self._timestamp_label)

        metadata_frame = QWidget(self)
        metadata_layout = QVBoxLayout(metadata_frame)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(6)
        metadata_layout.addWidget(self._camera_label)
        metadata_layout.addWidget(self._lens_label)
        metadata_layout.addWidget(self._summary_label)
        content_layout.addWidget(metadata_frame)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(separator)

        exposure_container = QWidget(self)
        exposure_layout = QHBoxLayout(exposure_container)
        exposure_layout.setContentsMargins(0, 0, 0, 0)
        exposure_layout.addWidget(self._exposure_label)
        content_layout.addWidget(exposure_container)

        # AI-generated metadata section
        self._ai_separator = QFrame(self)
        self._ai_separator.setFrameShape(QFrame.HLine)
        self._ai_separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(self._ai_separator)

        self._ai_container = QWidget(self)
        ai_layout = QVBoxLayout(self._ai_container)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(6)

        self._caption_label = self._make_content_label()
        self._caption_label.setWordWrap(True)
        ai_layout.addWidget(self._caption_label)

        self._tags_container = QWidget(self)
        self._tags_layout = QHBoxLayout(self._tags_container)
        self._tags_layout.setContentsMargins(0, 0, 0, 0)
        self._tags_layout.setSpacing(4)
        ai_layout.addWidget(self._tags_container)

        content_layout.addWidget(self._ai_container)

        self._face_separator = QFrame(self)
        self._face_separator.setFrameShape(QFrame.HLine)
        self._face_separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(self._face_separator)

        self._face_container = QWidget(self)
        self._face_layout = QHBoxLayout(self._face_container)
        self._face_layout.setContentsMargins(0, 0, 0, 0)
        self._face_layout.setSpacing(8)
        self._face_layout.addStretch(1)
        self._face_add_button = QToolButton(self._face_container)
        self._face_add_button.setIconSize(_FACE_ADD_ICON_SIZE)
        self._face_add_button.setFixedSize(_FACE_ADD_BUTTON_SIZE)
        self._face_add_button.setAutoRaise(True)
        self._face_add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._face_add_button.setToolTip("Add face annotation")
        self._face_add_button.setStyleSheet(
            "QToolButton { padding: 0px; margin: 0px; border: none; background: transparent; }"
        )
        self._face_add_button.clicked.connect(self.manualFaceAddRequested.emit)
        self._update_face_add_button_icon()
        content_layout.addWidget(self._face_container)

        self._location_separator = QFrame(self)
        self._location_separator.setFrameShape(QFrame.HLine)
        self._location_separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(self._location_separator)

        self._location_container = QWidget(self)
        self._location_layout = QVBoxLayout(self._location_container)
        self._location_layout.setContentsMargins(0, 0, 0, 0)
        self._location_layout.setSpacing(8)

        self._location_fallback_label = QLabel(self._location_container)
        self._location_fallback_label.setWordWrap(True)
        self._location_fallback_label.hide()
        self._location_layout.addWidget(self._location_fallback_label)

        self._location_download_button = QPushButton("Download Map Extension", self._location_container)
        self._location_download_button.setAutoDefault(False)
        self._location_download_button.setDefault(False)
        self._location_download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._location_download_button.clicked.connect(self.downloadMapExtensionRequested.emit)
        self._location_download_button.hide()
        self._location_layout.addWidget(self._location_download_button, 0, Qt.AlignmentFlag.AlignLeft)

        self._location_editor_row = QWidget(self._location_container)
        self._location_editor_layout = QHBoxLayout(self._location_editor_row)
        self._location_editor_layout.setContentsMargins(0, 0, 0, 0)
        self._location_editor_layout.setSpacing(8)

        self._location_editor = QLineEdit(self._location_editor_row)
        self._location_editor.setClearButtonEnabled(True)
        self._location_editor.setCursor(Qt.CursorShape.IBeamCursor)
        self._location_editor.setPlaceholderText("Assign a Location")
        self._location_editor.installEventFilter(self)
        self._location_editor.textEdited.connect(self._handle_location_text_edited)
        self._location_editor_layout.addWidget(self._location_editor, 1)

        self._location_confirm_button = QPushButton("Confirm", self._location_editor_row)
        self._location_confirm_button.setAutoDefault(False)
        self._location_confirm_button.setDefault(False)
        self._location_confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._location_confirm_button.setEnabled(False)
        self._location_confirm_button.clicked.connect(self._emit_location_confirm_requested)
        self._location_editor_layout.addWidget(self._location_confirm_button, 0)
        self._location_layout.addWidget(self._location_editor_row)

        self._location_results = QListWidget(self._location_container)
        self._location_results.setAlternatingRowColors(True)
        self._location_results.setMaximumHeight(150)
        self._location_results.hide()
        self._location_results.installEventFilter(self)
        self._location_results.itemClicked.connect(self._handle_location_item_clicked)
        self._location_results.itemActivated.connect(self._handle_location_item_activated)
        self._location_results.currentRowChanged.connect(self._handle_location_row_changed)
        self._location_layout.addWidget(self._location_results)

        self._location_map = InfoLocationMapView(self._location_container)
        self._location_map.hide()
        self._location_layout.addWidget(self._location_map)
        content_layout.addWidget(self._location_container)

        layout.addWidget(content, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def current_rel(self) -> Optional[str]:
        """Return the relative path associated with the displayed asset."""

        return self._current_rel

    @property
    def close_button(self) -> QToolButton:
        """Expose the close button for external signal wiring."""

        return self._close_button

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _update_face_add_button_icon(self) -> None:
        self._face_add_button.setIcon(
            load_icon(
                "plus.circle.svg",
                color=self._resolve_action_icon_tint(),
                size=(_FACE_ADD_ICON_SIZE.width(), _FACE_ADD_ICON_SIZE.height()),
            )
        )

    def _rebuild_face_strip(self) -> None:
        while self._face_layout.count() > 0:
            item = self._face_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self._face_add_button:
                    widget.hide()
                else:
                    widget.deleteLater()
        if not self._asset_faces:
            self._face_separator.setVisible(False)
            self._face_container.setVisible(False)
            return
        for annotation in self._asset_faces:
            self._face_layout.addWidget(self._make_face_avatar(annotation))
        self._face_add_button.show()
        self._face_layout.addWidget(self._face_add_button)
        self._face_layout.addStretch(1)
        self._face_separator.setVisible(True)
        self._face_container.setVisible(True)

    def _make_face_avatar(self, annotation: AssetFaceAnnotation) -> QLabel:
        label = _FaceAvatarWidget(annotation, self._face_action_candidates, self._face_container)
        label.deleteRequested.connect(self.faceDeleteRequested.emit)
        label.moveRequested.connect(self.faceMoveRequested.emit)
        label.newPersonRequested.connect(self.faceMoveToNewPersonRequested.emit)
        return label

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        is_title_target = watched in (self._title_bar, self._title_label)
        is_drag_event = event.type() in self._DRAG_EVENT_TYPES
        if is_title_target and is_drag_event:
            mouse_event = event  # type: ignore[assignment]
            if event.type() == QEvent.Type.MouseButtonPress and self._begin_drag(mouse_event):
                mouse_event.accept()
                return True
            if event.type() == QEvent.Type.MouseMove and self._continue_drag(mouse_event):
                mouse_event.accept()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and self._end_drag():
                mouse_event.accept()
                return True
        if watched in (self._location_editor, self._location_results):
            if event.type() == QEvent.Type.ShortcutOverride:
                key_event = event  # type: ignore[assignment]
                if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                    key_event.accept()
                    return True
            if event.type() == QEvent.Type.KeyPress:
                key_event = event  # type: ignore[assignment]
                handled = (
                    self._handle_location_editor_key_press(key_event)
                    if watched is self._location_editor
                    else self._handle_location_results_key_press(key_event)
                )
                if handled:
                    key_event.accept()
                    return True
            if event.type() == QEvent.Type.KeyRelease:
                key_event = event  # type: ignore[assignment]
                if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                    key_event.accept()
                    return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_close_button_style()
            self._update_face_add_button_icon()
        super().changeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        """Centre the panel over its parent window the first time it appears."""

        super().showEvent(event)
        first_show = not self._centered
        if first_show:
            self._centered = True
        # Use only the deferred reflow to avoid a double layout pass.
        self._schedule_post_show_reflow(recenter=first_show)

    def closeEvent(self, event) -> None:
        """Emit a dismissal signal so the detail state stays in sync."""

        self.shutdown()
        self.dismissed.emit()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        """Draw drop shadow and an anti-aliased rounded rectangle."""

        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        s = self._SHADOW_SIZE
        content_rect = QRectF(self.rect()).adjusted(0, 0, -s, -s)
        radius = min(
            self._CORNER_RADIUS,
            min(content_rect.width(), content_rect.height()) / 2.0,
        )

        # -- drop shadow (right + bottom only) -----------------------------
        shadow_steps = s
        for i in range(shadow_steps):
            alpha = int(self._SHADOW_MAX_ALPHA * (1 - i / shadow_steps) ** 2)
            if alpha <= 0:
                continue
            shadow_color = QColor(0, 0, 0, alpha)
            spread = float(i)
            shadow_rect = content_rect.adjusted(spread, spread, spread, spread)
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(
                shadow_rect,
                radius + spread * self._SHADOW_RADIUS_GROWTH,
                radius + spread * self._SHADOW_RADIUS_GROWTH,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(shadow_path, shadow_color)

        # -- background ----------------------------------------------------
        path = QPainterPath()
        path.addRoundedRect(content_rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        bg_color = self.palette().color(QPalette.ColorRole.Window)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, bg_color)

        border_color = self.palette().color(QPalette.ColorRole.Mid)
        border_color.setAlpha(80)
        painter.setPen(border_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin a drag when clicking on the title bar area."""

        if event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            if self._title_bar.geometry().contains(local_pos):
                if self._begin_drag(event):
                    event.accept()
                    return
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move the panel when dragging the title bar."""

        if self._continue_drag(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End a title-bar drag."""

        if self._end_drag():
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _avatar_pixmap(path: Path | None) -> QPixmap | None:
    if path is None or not path.exists():
        return None
    source = QPixmap(str(path))
    if source.isNull():
        return None
    size = _FACE_AVATAR_DIAMETER
    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0.0, 0.0, float(size), float(size)))
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return rounded
