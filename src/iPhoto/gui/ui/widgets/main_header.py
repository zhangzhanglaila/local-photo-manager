"""Header row containing the menu bar and primary toolbar buttons."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenuBar,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon
from .search_popup import SearchPopup


class SearchInput(QLineEdit):
    """Search input widget with placeholder text and clear button."""

    # Signal emitted when user presses Enter
    search_submitted = Signal(str)
    # The dropdown wants to be shown (focus / click on the field).
    popup_requested = Signal()
    escape_pressed = Signal()

    # macOS-style search field: a rounded, slightly recessed capsule that turns
    # white with a hairline accent border while focused.
    SEARCH_FIELD_STYLESHEET = (
        "QLineEdit#searchInput {"
        "  background-color: rgba(0, 0, 0, 0.055);"
        "  border: 1px solid rgba(0, 0, 0, 0.08);"
        "  border-radius: 7px;"
        "  padding: 2px 8px 2px 4px;"
        "  selection-background-color: #007AFF;"
        "  selection-color: white;"
        "}"
        "QLineEdit#searchInput:hover {"
        "  background-color: rgba(0, 0, 0, 0.075);"
        "}"
        "QLineEdit#searchInput:focus {"
        "  background-color: #FFFFFF;"
        "  border: 1px solid rgba(0, 122, 255, 0.55);"
        "}"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(tr("search.placeholder", default="搜索照片（如：海边、黄色、狗）..."))
        self.setObjectName("searchInput")
        self.setClearButtonEnabled(True)
        self.setFixedWidth(560)
        self.setFixedHeight(32)
        self.setStyleSheet(self.SEARCH_FIELD_STYLESHEET)
        # The magnifier lives inside the field, like the macOS search field,
        # instead of sitting next to it as a separate emoji label.
        self._leading_action = self.addAction(
            load_icon("magnifyingglass.svg", size=(13, 13)),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self._leading_action.setEnabled(False)

        # Connect Enter key to signal
        self.returnPressed.connect(self._on_return_pressed)

    def _on_return_pressed(self) -> None:
        """Handle Enter key press."""
        text = self.text().strip()
        if text:
            self.search_submitted.emit(text)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Opening the suggestion list is tied to a deliberate click (or Ctrl+F).
        # Focus alone is not intent: hiding the view that owned focus hands it
        # back to this field — e.g. clicking "People" — and the dropdown used to
        # pop open every time that happened.
        super().mousePressEvent(event)
        self.popup_requested.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MainHeaderWidget(QWidget):
    """Container hosting the menu bar alongside quick access buttons."""

    # Signal for search queries
    search_requested = Signal(str)
    # Same query plus the scope chosen in the dropdown ("all" or "album").
    search_submitted_with_scope = Signal(str, str)
    search_popup_opened = Signal()
    similar_search_requested = Signal(str)
    search_history_clear_requested = Signal()

    def __init__(self, parent: QWidget | None, main_window: QWidget) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self._search_popup: SearchPopup | None = None
        self.setObjectName("menuBarContainer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        # Left margin leaves room for the traffic lights, the right margin keeps
        # the search field off the window edge.
        layout.setContentsMargins(16, 7, 14, 7)
        layout.setSpacing(8)

        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName("chromeMenuBar")
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.menu_bar.setAutoFillBackground(True)
        self.menu_bar.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        layout.addWidget(self.menu_bar)

        # Add search input with icon
        search_container = QWidget(self)
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(4)

        # Search icon
        self.search_icon = QLabel("🔍", self)
        self.search_icon.setStyleSheet("font-size: 14px;")
        search_layout.addWidget(self.search_icon)
        # The magnifier now lives inside the search field itself, so the emoji
        # label is only kept for compatibility and never shown.
        self.search_icon.hide()

        # Search input
        self.search_input = SearchInput(self)
        self.search_input.search_submitted.connect(self._on_search_submitted)
        self.search_input.popup_requested.connect(self._show_search_popup)
        self.search_input.escape_pressed.connect(self._hide_search_popup)
        search_layout.addWidget(self.search_input)

        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, main_window)
        find_shortcut.activated.connect(self.focus_search)
        self._find_shortcut = find_shortcut

        layout.addSpacerItem(
            QSpacerItem(
                1,
                1,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

        # Search sits in the middle of the toolbar, like a browser address bar.
        layout.addWidget(search_container)

        layout.addSpacerItem(
            QSpacerItem(
                1,
                1,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

        self.rescan_button = QToolButton(self)
        self.rescan_button.setObjectName("rescanButton")
        self.rescan_button.setAutoRaise(True)
        layout.addWidget(self.rescan_button)

        self.selection_button = QToolButton(self)
        self.selection_button.setObjectName("selectionButton")
        self.selection_button.setAutoRaise(True)
        self.selection_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        layout.addWidget(self.selection_button)

        self._synchronise_palettes()
        self._create_actions(main_window)
        self._populate_menus()
        # No in-window menu bar: the entries live in the Settings window, which
        # is opened from the sidebar's pinned row.
        self.menu_bar.setVisible(False)

    def attach_window_controls(self, controls: QWidget) -> None:
        """Host the caption buttons at the trailing edge of the header row.

        The reference title bar keeps the minimise / maximise / close controls on
        the right, after every toolbar action, instead of the macOS traffic
        lights on the left.
        """

        controls.setParent(self)
        layout = self.layout()
        if layout is not None:
            layout.addWidget(
                controls,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        self._window_controls = controls

    def _synchronise_palettes(self) -> None:
        """Ensure the container and menu bar share the same opaque background."""

        menu_palette = self.menu_bar.palette()
        menu_palette.setColor(
            QPalette.ColorRole.Window,
            menu_palette.color(QPalette.ColorRole.Base),
        )
        self.menu_bar.setPalette(menu_palette)

        container_palette = self.palette()
        container_palette.setColor(
            QPalette.ColorRole.Window,
            menu_palette.color(QPalette.ColorRole.Base),
        )
        self.setPalette(container_palette)

    def _create_actions(self, main_window: QWidget) -> None:
        """Instantiate the :class:`QAction` objects exposed to controllers."""

        self.open_album_action = QAction(tr("action.open_album"), main_window)
        self.rescan_action = QAction(tr("action.rescan"), main_window)
        self.rebuild_links_action = QAction(tr("action.rebuild_links"), main_window)
        self.bind_library_action = QAction(tr("action.set_basic_library"), main_window)
        self.download_map_extension_action = QAction(tr("action.download_map_ext"), main_window)
        self.toggle_filmstrip_action = QAction(
            tr("action.show_filmstrip"), main_window, checkable=True
        )
        self.toggle_filmstrip_action.setChecked(True)
        self.toggle_face_names_action = QAction(
            tr("action.show_face_names"), main_window, checkable=True
        )
        self.toggle_face_names_action.setChecked(False)
        self.toggle_hidden_people_action = QAction(
            tr("action.show_hidden_people"), main_window, checkable=True
        )
        self.toggle_hidden_people_action.setChecked(False)

        self.share_action_group = QActionGroup(main_window)
        self.share_action_copy_file = QAction(tr("action.copy_file"), main_window, checkable=True)
        self.share_action_copy_path = QAction(tr("action.copy_path"), main_window, checkable=True)
        self.share_action_reveal_file = QAction(
            tr("action.reveal_file"), main_window, checkable=True
        )
        self.share_action_group.addAction(self.share_action_copy_file)
        self.share_action_group.addAction(self.share_action_copy_path)
        self.share_action_group.addAction(self.share_action_reveal_file)
        self.share_action_reveal_file.setChecked(True)

        self.wheel_action_group = QActionGroup(main_window)
        self.wheel_action_navigate = QAction(tr("action.navigate"), main_window, checkable=True)
        self.wheel_action_zoom = QAction(tr("action.zoom"), main_window, checkable=True)
        self.wheel_action_group.addAction(self.wheel_action_navigate)
        self.wheel_action_group.addAction(self.wheel_action_zoom)
        self.wheel_action_navigate.setChecked(True)

        self.export_all_edited_action = QAction(tr("action.export_all_edited"), main_window)
        self.export_selected_action = QAction(tr("action.export_selected"), main_window)

        self.export_destination_group = QActionGroup(main_window)
        self.export_destination_library = QAction(tr("action.export_to_library"), main_window, checkable=True)
        self.export_destination_ask = QAction(tr("action.export_ask"), main_window, checkable=True)
        self.export_destination_group.addAction(self.export_destination_library)
        self.export_destination_group.addAction(self.export_destination_ask)
        self.export_destination_library.setChecked(True)

        self.export_format_group = QActionGroup(main_window)
        self.export_format_jpg = QAction("JPG", main_window, checkable=True)
        self.export_format_png = QAction("PNG", main_window, checkable=True)
        self.export_format_tiff = QAction("TIFF", main_window, checkable=True)
        self.export_format_group.addAction(self.export_format_jpg)
        self.export_format_group.addAction(self.export_format_png)
        self.export_format_group.addAction(self.export_format_tiff)
        self.export_format_jpg.setChecked(True)

        self.theme_group = QActionGroup(main_window)
        self.theme_system = QAction(tr("action.system_default"), main_window, checkable=True)
        self.theme_light = QAction(tr("action.light_mode"), main_window, checkable=True)
        self.theme_dark = QAction(tr("action.dark_mode"), main_window, checkable=True)
        self.theme_group.addAction(self.theme_system)
        self.theme_group.addAction(self.theme_light)
        self.theme_group.addAction(self.theme_dark)
        self.theme_system.setChecked(True)

        self.language_group = QActionGroup(main_window)
        self.language_zh = QAction(tr("action.lang_zh"), main_window, checkable=True)
        self.language_en = QAction(tr("action.lang_en"), main_window, checkable=True)
        self.language_group.addAction(self.language_zh)
        self.language_group.addAction(self.language_en)
        self.language_zh.setChecked(True)

        # Agent features toggle
        self.toggle_semantic_search_action = QAction(
            tr("action.enable_semantic_search"), main_window, checkable=True
        )
        self.toggle_semantic_search_action.setChecked(False)

        # Search precision tiers: stricter tiers keep fewer, more confident hits.
        self.search_precision_group = QActionGroup(main_window)
        self.search_precision_actions: dict[str, QAction] = {}
        for tier, label_key in (
            ("strict", "action.search_precision_strict"),
            ("balanced", "action.search_precision_balanced"),
            ("loose", "action.search_precision_loose"),
        ):
            action = QAction(tr(label_key), main_window, checkable=True)
            action.setData(tier)
            self.search_precision_group.addAction(action)
            self.search_precision_actions[tier] = action
        self.search_precision_actions["balanced"].setChecked(True)

        # Agent organize actions
        self.find_duplicates_action = QAction(tr("action.find_duplicates"), main_window)
        self.smart_album_event_action = QAction(tr("action.smart_album_event"), main_window)
        self.smart_album_location_action = QAction(tr("action.smart_album_location"), main_window)
        self.smart_album_time_action = QAction(tr("action.smart_album_time"), main_window)
        self.smart_album_theme_action = QAction(tr("action.smart_album_theme"), main_window)


    def _populate_menus(self) -> None:
        """Populate the menu bar and wire shared actions to widgets."""

        self._file_menu = self.menu_bar.addMenu(tr("menu.file"))
        for action in (
            self.open_album_action,
            None,
            self.bind_library_action,
            None,
            self.export_all_edited_action,
            self.export_selected_action,
            None,
            self.rebuild_links_action,
        ):
            if action is None:
                self._file_menu.addSeparator()
            else:
                self._file_menu.addAction(action)

        self.rescan_button.setDefaultAction(self.rescan_action)

        self._view_menu = self.menu_bar.addMenu(tr("menu.view"))
        self._view_menu.addAction(self.toggle_face_names_action)
        self._view_menu.addAction(self.toggle_hidden_people_action)
        self._view_menu.addSeparator()
        self._view_menu.addAction(self.toggle_filmstrip_action)

        # Agent organize features
        self._view_menu.addSeparator()
        self._agent_menu = self._view_menu.addMenu(tr("menu.agent_features"))
        self._agent_menu.addAction(self.find_duplicates_action)
        self._agent_menu.addAction(self.smart_album_event_action)
        self._agent_menu.addAction(self.smart_album_location_action)
        self._agent_menu.addAction(self.smart_album_time_action)
        self._agent_menu.addAction(self.smart_album_theme_action)

        self._settings_menu = self.menu_bar.addMenu(tr("menu.settings"))
        self.change_workspace_action = QAction(
            tr("action.change_workspace"), self
        )
        self._settings_menu.addAction(self.change_workspace_action)
        self.run_setup_action = QAction(tr("action.run_setup_wizard"), self)
        self._settings_menu.addAction(self.run_setup_action)
        self._settings_menu.addAction(self.bind_library_action)
        self._settings_menu.addAction(self.download_map_extension_action)
        self._settings_menu.addSeparator()

        self._appearance_menu = self._settings_menu.addMenu(tr("menu.appearance"))
        self._appearance_menu.addAction(self.theme_system)
        self._appearance_menu.addAction(self.theme_light)
        self._appearance_menu.addAction(self.theme_dark)

        self._export_dest_menu = self._settings_menu.addMenu(tr("menu.export_destination"))
        self._export_dest_menu.addAction(self.export_destination_library)
        self._export_dest_menu.addAction(self.export_destination_ask)

        self._export_fmt_menu = self._settings_menu.addMenu(tr("menu.export_format"))
        self._export_fmt_menu.addAction(self.export_format_jpg)
        self._export_fmt_menu.addAction(self.export_format_png)
        self._export_fmt_menu.addAction(self.export_format_tiff)

        self._wheel_menu = self._settings_menu.addMenu(tr("menu.wheel_action"))
        self._wheel_menu.addAction(self.wheel_action_navigate)
        self._wheel_menu.addAction(self.wheel_action_zoom)

        self._share_menu = self._settings_menu.addMenu(tr("menu.share_action"))
        self._share_menu.addAction(self.share_action_copy_file)
        self._share_menu.addAction(self.share_action_copy_path)
        self._share_menu.addAction(self.share_action_reveal_file)

        self._language_menu = self._settings_menu.addMenu(tr("menu.language"))
        self._language_menu.addAction(self.language_zh)
        self._language_menu.addAction(self.language_en)

        # Agent features
        self._settings_menu.addSeparator()
        self._settings_menu.addAction(self.toggle_semantic_search_action)
        self._search_precision_menu = self._settings_menu.addMenu(
            tr("menu.search_precision")
        )
        for tier in ("strict", "balanced", "loose"):
            self._search_precision_menu.addAction(
                self.search_precision_actions[tier]
            )

    def _on_search_submitted(self, text: str) -> None:
        popup = self._search_popup
        scope = popup.current_scope() if popup is not None else "all"
        self._hide_search_popup()
        self.search_requested.emit(text)
        self.search_submitted_with_scope.emit(text, scope)

    def _ensure_search_popup(self) -> SearchPopup:
        if self._search_popup is None:
            popup = SearchPopup(self._main_window)
            popup.query_selected.connect(self._on_history_query_selected)
            popup.similar_selected.connect(self._on_similar_selected)
            popup.clear_history_requested.connect(
                self.search_history_clear_requested.emit
            )
            self._search_popup = popup
        return self._search_popup

    def _on_history_query_selected(self, query: str) -> None:
        self.search_input.setText(query)
        self._on_search_submitted(query)

    def _on_similar_selected(self, asset_id: str) -> None:
        self._hide_search_popup()
        self.similar_search_requested.emit(asset_id)

    def _show_search_popup(self) -> None:
        popup = self._ensure_search_popup()
        self.search_popup_opened.emit()

        width = max(520, self.search_input.width())
        width = min(width, max(280, self._main_window.width() - 24))
        popup.setFixedWidth(width)
        popup.refresh_size()

        position = self.search_input.mapTo(
            self._main_window, QPoint(0, self.search_input.height() + 6)
        )
        if position.x() + width > self._main_window.width() - 12:
            position.setX(max(12, self._main_window.width() - width - 12))
        popup.move(position)
        popup.show()
        # Size after showing: nested layouts only report usable heights once
        # their geometry is live. A second pass on the next event-loop turn picks
        # up the real chip heights; the resize happens before the first paint.
        popup.refresh_size()
        QTimer.singleShot(0, popup.refresh_size)
        popup.raise_()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _hide_search_popup(self) -> None:
        if self._search_popup is not None:
            self._search_popup.hide()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        popup = self._search_popup
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and popup is not None
            and popup.isVisible()
        ):
            widget = QApplication.widgetAt(event.globalPosition().toPoint())
            inside = widget is not None and (
                widget is popup
                or popup.isAncestorOf(widget)
                or widget is self.search_input
            )
            if not inside:
                self._hide_search_popup()
        return super().eventFilter(watched, event)

    def focus_search(self) -> None:
        """Focus the search field and open its suggestions (Ctrl/Cmd+F)."""

        self.search_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_input.selectAll()
        self._show_search_popup()

    def set_search_history(self, queries) -> None:
        self._ensure_search_popup().set_history(queries)

    def set_album_scope(self, label: str | None) -> None:
        self._ensure_search_popup().set_album_scope(label)

    def set_similar_photos(self, items) -> None:
        self._ensure_search_popup().set_similar_photos(items)

    def add_similar_photo(self, asset_id: str, pixmap) -> None:
        self._ensure_search_popup().add_similar_photo(asset_id, pixmap)

    def retranslate(self) -> None:
        """Refresh all menu and action texts for the current language."""

        self.open_album_action.setText(tr("action.open_album"))
        self.rescan_action.setText(tr("action.rescan"))
        self.rebuild_links_action.setText(tr("action.rebuild_links"))
        self.bind_library_action.setText(tr("action.set_basic_library"))
        self.download_map_extension_action.setText(tr("action.download_map_ext"))
        self.toggle_filmstrip_action.setText(tr("action.show_filmstrip"))
        self.toggle_face_names_action.setText(tr("action.show_face_names"))
        self.toggle_hidden_people_action.setText(tr("action.show_hidden_people"))
        self.share_action_copy_file.setText(tr("action.copy_file"))
        self.share_action_copy_path.setText(tr("action.copy_path"))
        self.share_action_reveal_file.setText(tr("action.reveal_file"))
        self.wheel_action_navigate.setText(tr("action.navigate"))
        self.wheel_action_zoom.setText(tr("action.zoom"))
        self.export_all_edited_action.setText(tr("action.export_all_edited"))
        self.export_selected_action.setText(tr("action.export_selected"))
        self.export_destination_library.setText(tr("action.export_to_library"))
        self.export_destination_ask.setText(tr("action.export_ask"))
        self.theme_system.setText(tr("action.system_default"))
        self.theme_light.setText(tr("action.light_mode"))
        self.theme_dark.setText(tr("action.dark_mode"))
        self.language_zh.setText(tr("action.lang_zh"))
        self.language_en.setText(tr("action.lang_en"))
        self.toggle_semantic_search_action.setText(tr("action.enable_semantic_search"))
        self.search_precision_actions["strict"].setText(
            tr("action.search_precision_strict")
        )
        self.search_precision_actions["balanced"].setText(
            tr("action.search_precision_balanced")
        )
        self.search_precision_actions["loose"].setText(
            tr("action.search_precision_loose")
        )
        self.find_duplicates_action.setText(tr("action.find_duplicates"))
        self.smart_album_event_action.setText(tr("action.smart_album_event"))
        self.smart_album_location_action.setText(tr("action.smart_album_location"))
        self.smart_album_time_action.setText(tr("action.smart_album_time"))
        self.smart_album_theme_action.setText(tr("action.smart_album_theme"))

        if self._search_popup is not None:
            self._search_popup.retranslate()

        # Menu titles (with safety checks for deleted objects)
        try:
            if hasattr(self, '_file_menu') and self._file_menu is not None:
                self._file_menu.setTitle(tr("menu.file"))
            if hasattr(self, '_view_menu') and self._view_menu is not None:
                self._view_menu.setTitle(tr("menu.view"))
            if hasattr(self, '_settings_menu') and self._settings_menu is not None:
                self._settings_menu.setTitle(tr("menu.settings"))
            if (
                hasattr(self, '_search_precision_menu')
                and self._search_precision_menu is not None
            ):
                self._search_precision_menu.setTitle(tr("menu.search_precision"))
            if hasattr(self, '_appearance_menu') and self._appearance_menu is not None:
                self._appearance_menu.setTitle(tr("menu.appearance"))
            if hasattr(self, '_export_dest_menu') and self._export_dest_menu is not None:
                self._export_dest_menu.setTitle(tr("menu.export_destination"))
            if hasattr(self, '_export_fmt_menu') and self._export_fmt_menu is not None:
                self._export_fmt_menu.setTitle(tr("menu.export_format"))
            if hasattr(self, '_wheel_menu') and self._wheel_menu is not None:
                self._wheel_menu.setTitle(tr("menu.wheel_action"))
            if hasattr(self, '_share_menu') and self._share_menu is not None:
                self._share_menu.setTitle(tr("menu.share_action"))
            if hasattr(self, '_language_menu') and self._language_menu is not None:
                self._language_menu.setTitle(tr("menu.language"))
        except RuntimeError:
            # Menu objects may have been deleted
            pass


__all__ = ["MainHeaderWidget"]
