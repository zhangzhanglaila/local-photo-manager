"""Header row containing the menu bar and primary toolbar buttons."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMenuBar,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QWidget,
)

from ....i18n import tr
from ..icon import load_icon


class SearchInput(QLineEdit):
    """Search input widget with placeholder text and clear button."""

    # Signal emitted when user presses Enter
    search_submitted = Signal(str)

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
        self.setMinimumWidth(200)
        self.setMaximumWidth(320)
        self.setFixedHeight(30)
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


class MainHeaderWidget(QWidget):
    """Container hosting the menu bar alongside quick access buttons."""

    # Signal for search queries
    search_requested = Signal(str)

    def __init__(self, parent: QWidget | None, main_window: QWidget) -> None:
        super().__init__(parent)
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
        self.search_input.search_submitted.connect(self.search_requested.emit)
        search_layout.addWidget(self.search_input)

        layout.addSpacerItem(
            QSpacerItem(
                1,
                1,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

        # Search sits at the trailing edge like a macOS toolbar.
        layout.addWidget(search_container)

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
        self._install_compact_menu_button()

    def attach_window_controls(self, controls: QWidget) -> None:
        """Host the traffic lights in the header row.

        macOS keeps window controls and toolbar items on one row; a separate title
        bar above the toolbar is what makes the window look like a Windows app.
        """

        controls.setParent(self)
        layout = self.layout()
        if layout is not None:
            layout.insertWidget(
                0,
                controls,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
        self._window_controls = controls

    def _install_compact_menu_button(self) -> None:
        """Replace the in-window menu bar with a single "⋯" toolbar button.

        macOS puts application menus in the system menu bar, so showing
        File/View/Settings as an in-window menu bar is what makes the window read
        as a Windows/Linux app. The actions themselves stay on the window, which
        keeps their shortcuts working; only the presentation changes.
        """

        self.more_button = QToolButton(self)
        self.more_button.setObjectName("moreMenuButton")
        self.more_button.setAutoRaise(True)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.setIcon(load_icon("ellipsis.svg", size=(18, 18)))
        self.more_button.setIconSize(QSize(18, 18))
        self.more_button.setFixedSize(QSize(30, 28))
        self.more_button.setToolTip(tr("menu.more", default="More"))
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setStyleSheet(
            "QToolButton#moreMenuButton {"
            "  border: none;"
            "  border-radius: 7px;"
            "  background: transparent;"
            "}"
            "QToolButton#moreMenuButton:hover {"
            "  background-color: rgba(0, 0, 0, 0.07);"
            "}"
            "QToolButton#moreMenuButton:pressed,"
            "QToolButton#moreMenuButton:open {"
            "  background-color: rgba(0, 0, 0, 0.12);"
            "}"
        )

        # Populate eagerly: building the menu from ``aboutToShow`` produced an
        # empty popup, because Qt sizes the popup before that signal.
        #
        # The menus are *reused* (``addMenu(QMenu)``) rather than copied action by
        # action. Copying the actions into fresh menus destroyed the originals:
        # sharing a menu's child actions between two menus makes Qt tear down the
        # source menus, which then broke ``retranslate()`` and the menu bar.
        self.more_menu = QMenu(self.more_button)
        for menu_action in self.menu_bar.actions():
            source = menu_action.menu()
            if source is not None:
                self.more_menu.addMenu(source)
        self.more_button.setMenu(self.more_menu)

        self.menu_bar.setVisible(False)
        layout = self.layout()
        if layout is not None:
            layout.insertWidget(0, self.more_button)

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
        self.find_duplicates_action.setText(tr("action.find_duplicates"))
        self.smart_album_event_action.setText(tr("action.smart_album_event"))
        self.smart_album_location_action.setText(tr("action.smart_album_location"))
        self.smart_album_time_action.setText(tr("action.smart_album_time"))
        self.smart_album_theme_action.setText(tr("action.smart_album_theme"))

        # Menu titles (with safety checks for deleted objects)
        try:
            if hasattr(self, '_file_menu') and self._file_menu is not None:
                self._file_menu.setTitle(tr("menu.file"))
            if hasattr(self, '_view_menu') and self._view_menu is not None:
                self._view_menu.setTitle(tr("menu.view"))
            if hasattr(self, '_settings_menu') and self._settings_menu is not None:
                self._settings_menu.setTitle(tr("menu.settings"))
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
            # The compact "⋯" menu reuses these very QMenu objects, so their
            # titles update automatically.
        except RuntimeError:
            # Menu objects may have been deleted
            pass


__all__ = ["MainHeaderWidget"]
