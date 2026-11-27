"""Location editing for the floating info panel.

The panel embeds a search editor, a suggestion list and a preview map; this
mixin owns their state machine and keeps ``InfoPanel`` focused on layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QListWidgetItem, QWidget

if TYPE_CHECKING:  # pragma: no cover
    from ....application.ports import MapRuntimePort


class InfoPanelLocationMixin:
    """Extracted from :class:`InfoPanel`."""

    def set_location_capability(
        self,
        *,
        enabled: bool,
        preview_enabled: bool | None = None,
        fallback_text: str | None = None,
    ) -> None:
        self._location_capability_enabled = bool(enabled)
        self._location_preview_enabled = (
            bool(preview_enabled)
            if preview_enabled is not None
            else self._location_capability_enabled
        )
        if isinstance(fallback_text, str) and fallback_text.strip():
            self._location_fallback_text = fallback_text.strip()
        self._apply_location_metadata(self._metadata or {}, previous_rel=self._current_rel)
        self._refresh_or_schedule_panel_geometry()



    def set_map_runtime(self, map_runtime: MapRuntimePort | None) -> None:
        """Forward the active session map runtime to the embedded mini-map."""

        self._location_map.set_map_runtime(map_runtime)
        self._apply_location_metadata(self._metadata or {}, previous_rel=self._current_rel)
        self._refresh_or_schedule_panel_geometry()



    def set_location_suggestions(self, suggestions: list[object]) -> None:
        self._location_suggestions = list(suggestions)
        self._location_results.clear()
        if not self._location_capability_enabled or not self._location_suggestions:
            self._selected_location_suggestion = None
            self._set_widget_explicitly_visible(self._location_results, False)
            self._location_confirm_button.setEnabled(False)
            self._refresh_or_schedule_panel_geometry()
            return

        for index, suggestion in enumerate(self._location_suggestions):
            display_name = str(getattr(suggestion, "display_name", "") or "").strip()
            secondary_text = str(getattr(suggestion, "secondary_text", "") or "").strip()
            if not display_name:
                continue
            item = QListWidgetItem(display_name if not secondary_text else f"{display_name}\n{secondary_text}")
            item.setToolTip(secondary_text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self._location_results.addItem(item)

        if self._location_results.count() <= 0:
            self._selected_location_suggestion = None
            self._set_widget_explicitly_visible(self._location_results, False)
            self._location_confirm_button.setEnabled(False)
            self._refresh_or_schedule_panel_geometry()
            return

        self._location_results.setCurrentRow(0)
        self._set_widget_explicitly_visible(self._location_results, True)
        self._location_confirm_button.setEnabled(True)
        self._refresh_or_schedule_panel_geometry()



    def set_location_busy(self, busy: bool) -> None:
        self._location_editor.setEnabled(self._location_capability_enabled)
        self._location_editor.setReadOnly(bool(busy) and self._location_capability_enabled)
        self._location_editor.setClearButtonEnabled(self._location_capability_enabled and not busy)
        self._location_confirm_button.setEnabled(
            not busy and self._location_capability_enabled and self._selected_location_suggestion is not None
        )
        self._location_confirm_button.setText("Assigning..." if busy else "Confirm")



    def preview_location(self, display_name: str, latitude: float, longitude: float) -> None:
        metadata = dict(self._metadata or {})
        metadata["location"] = display_name
        metadata["place"] = display_name
        metadata["gps"] = {
            "lat": float(latitude),
            "lon": float(longitude),
        }
        self._metadata = metadata
        self._updating_location_ui = True
        try:
            self._location_editor.setText(display_name)
        finally:
            self._updating_location_ui = False
        self._location_dirty = False
        self._location_editor.setPlaceholderText("")
        self._clear_location_results()
        self._show_location_map(float(latitude), float(longitude))
        self._refresh_or_schedule_panel_geometry()



    def shutdown(self) -> None:
        self._location_map.shutdown()



    def _apply_location_metadata(
        self,
        metadata: Mapping[str, Any],
        *,
        previous_rel: str | None,
    ) -> None:
        if previous_rel != self._current_rel:
            self._clear_location_results()
            self._location_dirty = False
        gps = metadata.get("gps")
        has_valid_gps = False
        if isinstance(gps, dict):
            latitude = gps.get("lat")
            longitude = gps.get("lon")
            has_valid_gps = isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))

        if not self._location_capability_enabled:
            self._clear_location_results()
            self._set_widget_explicitly_visible(self._location_editor_row, False)
            self._set_widget_explicitly_visible(self._location_results, False)
            if self._location_preview_enabled:
                self._set_widget_explicitly_visible(self._location_fallback_label, False)
                self._set_widget_explicitly_visible(self._location_download_button, False)
                if has_valid_gps:
                    assert isinstance(gps, dict)
                    self._show_location_map(float(gps["lat"]), float(gps["lon"]))
                else:
                    self._hide_location_map(clear=True)
            else:
                self._hide_location_map(clear=False)
                self._location_fallback_label.setText(self._location_fallback_text)
                self._set_widget_explicitly_visible(self._location_fallback_label, True)
                self._set_widget_explicitly_visible(self._location_download_button, True)
            return

        self._set_widget_explicitly_visible(self._location_fallback_label, False)
        self._set_widget_explicitly_visible(self._location_download_button, False)
        self._set_widget_explicitly_visible(self._location_editor_row, True)
        location_text = metadata.get("location") or metadata.get("place")
        normalized_location = (
            str(location_text).strip()
            if isinstance(location_text, str) and str(location_text).strip()
            else ""
        )
        should_replace_editor_text = (
            previous_rel != self._current_rel
            or (not self._location_editor.hasFocus() and not self._location_dirty)
        )
        if should_replace_editor_text:
            self._updating_location_ui = True
            try:
                self._location_editor.setText(normalized_location)
            finally:
                self._updating_location_ui = False
            self._location_dirty = False
        self._location_editor.setPlaceholderText(
            "Assign a Location" if not normalized_location else ""
        )

        if has_valid_gps:
            assert isinstance(gps, dict)
            self._show_location_map(float(gps["lat"]), float(gps["lon"]))
        else:
            self._hide_location_map(clear=True)



    def _handle_location_text_edited(self, text: str) -> None:
        if self._updating_location_ui:
            return
        self._location_dirty = True
        self._selected_location_suggestion = None
        self._location_confirm_button.setEnabled(False)
        if not text.strip():
            self._clear_location_results()
        self.locationQueryChanged.emit(text)



    def _handle_location_item_clicked(self, item: QListWidgetItem) -> None:
        self._select_location_item(item, update_editor=True)



    def _handle_location_item_activated(self, item: QListWidgetItem) -> None:
        self._select_location_item(item, update_editor=True)
        self._queue_location_confirm_requested()



    def _handle_location_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._location_suggestions):
            self._selected_location_suggestion = None
            self._location_confirm_button.setEnabled(False)
            return
        suggestion = self._location_suggestions[row]
        self._selected_location_suggestion = suggestion
        self.locationSuggestionActivated.emit(suggestion)
        self._location_confirm_button.setEnabled(True)



    def _select_location_item(self, item: QListWidgetItem, *, update_editor: bool) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0 or index >= len(self._location_suggestions):
            return
        self._location_results.setCurrentItem(item)
        suggestion = self._location_suggestions[index]
        self._selected_location_suggestion = suggestion
        if update_editor:
            display_name = str(getattr(suggestion, "display_name", "") or "").strip()
            if display_name:
                self._updating_location_ui = True
                try:
                    self._location_editor.setText(display_name)
                finally:
                    self._updating_location_ui = False
                self._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self._location_dirty = True
        self.locationSuggestionActivated.emit(suggestion)
        self._location_confirm_button.setEnabled(True)



    def _queue_location_confirm_requested(self) -> None:
        if self._location_confirm_queued:
            return
        self._location_confirm_queued = True
        QTimer.singleShot(0, self._emit_location_confirm_requested)



    def _emit_location_confirm_requested(self) -> None:
        self._location_confirm_queued = False
        if not self._location_capability_enabled:
            return
        query = self._location_editor.text().strip()
        if not query:
            return
        suggestion = self._selected_location_suggestion
        if suggestion is None and self._location_results.count() > 0:
            row = self._location_results.currentRow()
            if row < 0:
                row = 0
            if 0 <= row < len(self._location_suggestions):
                suggestion = self._location_suggestions[row]
        if suggestion is None:
            return
        self._clear_location_results()
        self.locationConfirmRequested.emit(query, suggestion)



    def _clear_location_results(self) -> None:
        self._location_suggestions = []
        self._selected_location_suggestion = None
        self._location_results.clear()
        self._set_widget_explicitly_visible(self._location_results, False)
        self._location_confirm_button.setEnabled(False)



    @staticmethod
    def _set_widget_explicitly_visible(widget: QWidget, visible: bool) -> None:
        if visible:
            if widget.isHidden():
                widget.show()
            return
        if not widget.isHidden():
            widget.hide()



    def _show_location_map(self, latitude: float, longitude: float) -> None:
        target = (float(latitude), float(longitude))
        current_lat, current_lon = self._location_map.current_location()
        should_update_location = (
            self._location_map.map_widget() is None
            or self._last_location_map_target is None
            or current_lat is None
            or current_lon is None
            or abs(self._last_location_map_target[0] - target[0]) > 1e-6
            or abs(self._last_location_map_target[1] - target[1]) > 1e-6
            or abs(float(current_lat) - target[0]) > 1e-6
            or abs(float(current_lon) - target[1]) > 1e-6
        )
        if should_update_location:
            self._location_map.set_location(target[0], target[1])
            self._last_location_map_target = target
        self._set_widget_explicitly_visible(self._location_map, True)



    def _hide_location_map(self, *, clear: bool) -> None:
        if clear:
            self._location_map.clear_location()
            self._last_location_map_target = None
        self._set_widget_explicitly_visible(self._location_map, False)



    def _handle_location_editor_key_press(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key not in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        ):
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._queue_location_confirm_requested()
            return True
        if self._location_results.count() <= 0:
            return key == Qt.Key.Key_Escape
        if key == Qt.Key.Key_Escape:
            self._clear_location_results()
            return True

        current_row = self._location_results.currentRow()
        if current_row < 0:
            current_row = 0
        if key == Qt.Key.Key_Down:
            current_row = min(self._location_results.count() - 1, current_row + 1)
        else:
            current_row = max(0, current_row - 1)
        self._location_results.setCurrentRow(current_row)
        return True



    def _handle_location_results_key_press(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._queue_location_confirm_requested()
            return True
        if key == Qt.Key.Key_Escape:
            self._clear_location_results()
            self._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
            return True
        return False

