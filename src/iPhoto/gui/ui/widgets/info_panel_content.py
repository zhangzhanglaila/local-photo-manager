"""Asset content population for the floating info panel.

These entry points turn a metadata mapping, face annotations and AI tags into
the labels the panel paints.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from PySide6.QtWidgets import QLabel

from iPhoto.people.records import AssetFaceAnnotation, PersonSummary


class InfoPanelContentMixin:
    """Extracted from :class:`InfoPanel`."""

    def set_asset_metadata(self, metadata: Mapping[str, Any]) -> None:
        """Populate the panel with information extracted from *metadata*."""

        previous_rel = self._current_rel
        self._metadata = dict(metadata)
        self._current_rel = str(metadata.get("rel") or metadata.get("name") or "") or None

        formatted = self._format_metadata(metadata)
        self._filename_label.setText(formatted.name)
        self._timestamp_label.setText(formatted.timestamp)
        self._camera_label.setVisible(bool(formatted.camera))
        self._camera_label.setText(formatted.camera)
        self._lens_label.setVisible(bool(formatted.lens))
        self._lens_label.setText(formatted.lens)
        self._summary_label.setVisible(bool(formatted.summary))
        self._summary_label.setText(formatted.summary)
        if formatted.exposure_line:
            self._exposure_label.setText(formatted.exposure_line)
        else:
            is_loading = bool(metadata.get("_metadata_loading"))
            fallback = (
                "Loading detailed video information..."
                if formatted.is_video and is_loading
                else "Loading detailed exposure information..."
                if is_loading
                else "Detailed video information is unavailable."
                if formatted.is_video
                else "Detailed exposure information is unavailable."
            )
            self._exposure_label.setText(fallback)
        self._apply_location_metadata(metadata, previous_rel=previous_rel)
        # Skip geometry refresh while metadata is still loading to avoid
        # a visible layout jump between the placeholder and final content.
        if not metadata.get("_metadata_loading"):
            self._refresh_or_schedule_panel_geometry()



    def set_asset_faces(self, annotations: list[AssetFaceAnnotation]) -> None:
        self._asset_faces = list(annotations)
        self._rebuild_face_strip()
        self._refresh_or_schedule_panel_geometry()



    def set_face_action_candidates(self, candidates: list[PersonSummary]) -> None:
        self._face_action_candidates = list(candidates)
        self._rebuild_face_strip()



    def set_asset_ai_metadata(self, caption: Optional[str] = None, tags: Optional[list] = None) -> None:
        """Set AI-generated metadata for the current asset.

        Parameters
        ----------
        caption : Optional[str]
            Generated caption text.
        tags : Optional[list]
            List of tag dictionaries with 'name' and 'confidence'.
        """
        # Update caption
        if caption:
            self._caption_label.setText(caption)
            self._caption_label.setVisible(True)
        else:
            self._caption_label.clear()
            self._caption_label.setVisible(False)

        # Clear existing tags
        while self._tags_layout.count() > 0:
            item = self._tags_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # Add new tags
        if tags:
            for tag in tags[:8]:  # Limit to 8 tags
                tag_name = tag.get("name", "")
                if not tag_name:
                    continue
                tag_label = QLabel(tag_name)
                tag_label.setStyleSheet(
                    "QLabel { background-color: palette(highlight); color: palette(highlighted-text); "
                    "border-radius: 8px; padding: 2px 8px; font-size: 11px; }"
                )
                self._tags_layout.addWidget(tag_label)
            self._tags_container.setVisible(True)
        else:
            self._tags_container.setVisible(False)

        # Show/hide the AI section
        has_ai_data = caption or tags
        self._ai_separator.setVisible(has_ai_data)
        self._ai_container.setVisible(has_ai_data)

        self._refresh_or_schedule_panel_geometry()



    def clear(self) -> None:
        """Reset the panel to an empty state without hiding the window."""

        self._metadata = None
        self._current_rel = None
        self._face_action_candidates = []
        for label in (
            self._filename_label,
            self._timestamp_label,
            self._camera_label,
            self._lens_label,
            self._summary_label,
            self._exposure_label,
        ):
            label.clear()
        self._exposure_label.setText("No metadata available for this item.")
        self._clear_location_results()
        self._location_map.clear_location()
        self._set_widget_explicitly_visible(self._location_map, False)
        self._set_widget_explicitly_visible(self._location_fallback_label, False)
        self._set_widget_explicitly_visible(self._location_download_button, False)
        self._location_editor_row.setVisible(self._location_capability_enabled)
        self._location_editor.clear()
        self._location_editor.setPlaceholderText("Assign a Location")
        self._location_editor.setReadOnly(False)
        self._location_editor.setClearButtonEnabled(True)
        self._location_confirm_button.setEnabled(False)
        self._location_confirm_button.setText("Confirm")
        self._location_dirty = False
        self._location_confirm_queued = False
        self._last_location_map_target = None
        self.set_asset_faces([])
        # Clear AI metadata
        self._caption_label.clear()
        self._caption_label.setVisible(False)
        while self._tags_layout.count() > 0:
            item = self._tags_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tags_container.setVisible(False)
        self._ai_separator.setVisible(False)
        self._ai_container.setVisible(False)
        self._refresh_or_schedule_panel_geometry()

