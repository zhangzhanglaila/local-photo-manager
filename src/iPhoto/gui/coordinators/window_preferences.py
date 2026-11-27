"""Window-level preference restore/persist, outside the main coordinator.

Wheel action, face-name visibility, hidden-people visibility, semantic-search
toggle/precision, playback volume and the sidebar checkbox filter are all
"settings <-> widgets" glue. ``MainCoordinator`` used to own that glue plus
file mutations and library opening; this object owns only the preference half.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction

from iPhoto.utils.env import is_truthy


class WindowPreferences(QObject):
    """Restore and persist the main window's user preferences."""

    def __init__(
        self,
        window,
        context,
        *,
        playback,
        search,
        gallery_vm,
        start_embedding_generation,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._playback = playback
        self._search = search
        self._gallery_vm = gallery_vm
        self._start_embedding_generation = start_embedding_generation

    def restore(self) -> None:
        """Restore wheel action, face/hidden people, search and volume state."""

        ui = self._window.ui
        settings = self._context.settings

        wheel_action = settings.get("ui.wheel_action", "navigate")
        if wheel_action == "zoom":
            ui.wheel_action_zoom.setChecked(True)
        else:
            wheel_action = "navigate"
            ui.wheel_action_navigate.setChecked(True)
        ui.image_viewer.set_wheel_action(wheel_action)

        show_face_names = self._as_bool(
            settings.get("ui.show_face_names_in_detail", False)
        )
        ui.toggle_face_names_action.setChecked(show_face_names)
        self._playback.set_face_name_display_enabled(show_face_names)

        show_hidden_people = self._as_bool(
            settings.get("ui.show_hidden_people", False)
        )
        ui.toggle_hidden_people_action.setChecked(show_hidden_people)
        ui.people_page.set_show_hidden_people(show_hidden_people)

        semantic_search_enabled = self._as_bool(
            settings.get("agent.semantic_search_enabled", False)
        )
        ui.main_header.toggle_semantic_search_action.setChecked(
            semantic_search_enabled
        )
        precision = self._search.search_precision()
        precision_action = getattr(ui.main_header, "search_precision_actions", {}).get(
            precision
        )
        if precision_action is not None:
            precision_action.setChecked(True)
        if semantic_search_enabled:
            self._start_embedding_generation()

        stored_volume = settings.get("ui.volume", 75)
        try:
            initial_volume = round(float(stored_volume))
        except (TypeError, ValueError):
            initial_volume = 75
        initial_volume = max(0, min(100, initial_volume))
        initial_muted = self._as_bool(settings.get("ui.is_muted", False))

        ui.video_area.set_volume(initial_volume)
        ui.video_area.set_muted(initial_muted)

    def on_wheel_action_changed(self, action: QAction) -> None:
        """Persist and apply the wheel action chosen from the menu."""

        ui = self._window.ui
        selected = "zoom" if action is ui.wheel_action_zoom else "navigate"
        if self._context.settings.get("ui.wheel_action") != selected:
            self._context.settings.set("ui.wheel_action", selected)
        ui.image_viewer.set_wheel_action(selected)

    def on_face_name_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_face_names_in_detail") != checked:
            self._context.settings.set("ui.show_face_names_in_detail", checked)
        self._playback.set_face_name_display_enabled(checked)

    def on_hidden_people_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_hidden_people") != checked:
            self._context.settings.set("ui.show_hidden_people", checked)
        self._window.ui.people_page.set_show_hidden_people(checked)

    def on_album_check_state_changed(self, checked_paths: list[Path]) -> None:
        """Persist the sidebar checkbox state and refresh the current view."""

        self._context.settings.set(
            "ui.checked_library_paths", [str(path) for path in checked_paths]
        )
        self._gallery_vm.on_album_check_state_changed(checked_paths)

    def restore_checked_library_paths(self) -> None:
        """Restore sidebar checkboxes, dropping roots the library no longer has."""

        stored = self._context.settings.get("ui.checked_library_paths", []) or []
        if not stored:
            return
        # A stored path can point at a root that was removed, or at the old root
        # after the library was rebound. Keeping it makes the SQL album filter
        # exclude every photo, so validate before restoring.
        resolved = [Path(path) for path in stored]
        library_roots = self._context.library.roots()
        root_set = {
            str(path.resolve()).lower().replace("\\", "/") for path in library_roots
        }
        valid_resolved = []
        for path in resolved:
            path_lower = (
                str(path.resolve()).lower().replace("\\", "/")
                if path.exists()
                else str(path).lower().replace("\\", "/")
            )
            if path_lower in root_set:
                valid_resolved.append(path)
                continue
            # Parent/child relationships are accepted as well.
            for root in root_set:
                if path_lower.startswith(root + "/") or root.startswith(path_lower + "/"):
                    valid_resolved.append(path)
                    break
        if not valid_resolved:
            self._context.settings.set("ui.checked_library_paths", [])
            return
        paths = {str(path) for path in valid_resolved}
        self._window.ui.sidebar.set_checked_root_paths(paths)
        self._gallery_vm.set_checked_album_paths(valid_resolved)

    @staticmethod
    def _as_bool(value: object) -> bool:
        if isinstance(value, str):
            return is_truthy(value)
        return bool(value)


__all__ = ["WindowPreferences"]
