"""The widget surface the main window promises to its coordinators.

``MainCoordinator`` used to guard every signal connection with
``hasattr(ui, "some_widget")``. `Ui_MainWindow.setupUi` creates all of those
attributes unconditionally, so the guards never fired — they only hid renames: a
widget or signal that disappeared would silently skip its connection with no
exception and no log line.

This module is the single, testable statement of that contract: the wiring test
instantiates the real window offscreen and asserts every name below exists. Add a
widget here when a coordinator starts using it; the test then protects it.
"""

from __future__ import annotations

from typing import Final

#: Widgets and actions coordinators touch directly (everything `setupUi` builds).
WIRED_UI_ATTRS: Final[tuple[str, ...]] = (
    "albums_dashboard_page",
    "back_button",
    "bind_library_action",
    "close_button",
    "detail_page",
    "download_map_extension_action",
    "edit_button",
    "edit_image_viewer",
    "edit_sidebar",
    "export_all_edited_action",
    "export_selected_action",
    "face_name_overlay",
    "favorite_button",
    "filmstrip_view",
    "fullscreen_button",
    "gallery_page",
    "grid_view",
    "image_viewer",
    "info_button",
    "info_panel",
    "language_en",
    "language_zh",
    "main_header",
    "map_button",
    "map_page",
    "map_panel",
    "map_view",
    "menu_bar",
    "minimize_button",
    "open_album_action",
    "people_page",
    "player_bar",
    "preview_window",
    "progress_bar",
    "rebuild_links_action",
    "rescan_action",
    "rescan_button",
    "rotate_left_button",
    "selection_button",
    "share_button",
    "sidebar",
    "size_grip",
    "splitter",
    "status_bar",
    "theme_dark",
    "theme_light",
    "theme_system",
    "timestamp_label",
    "title_bar",
    "toggle_face_names_action",
    "toggle_filmstrip_action",
    "toggle_hidden_people_action",
    "video_area",
    "video_trim_bar",
    "view_stack",
    "window_chrome",
    "window_controls",
    "window_shell",
    "window_title_label",
    "zoom_in_button",
    "zoom_out_button",
    "zoom_slider",
    "zoom_widget",
)

__all__ = ["WIRED_UI_ATTRS"]
