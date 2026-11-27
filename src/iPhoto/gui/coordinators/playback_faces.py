"""Face names on the photo, and the info panel's face actions.

Split out of ``PlaybackCoordinator``: the overlay that names people on the image,
plus rename/delete/move for the faces listed in the info panel. Bodies moved
verbatim.
"""

from __future__ import annotations

import logging
import sqlite3

from PySide6.QtCore import QTimer, Slot

from ...i18n import tr
from iPhoto.people.repository import AssetFaceAnnotation

from ..ui.widgets import dialogs

LOGGER = logging.getLogger(__name__)


class PlaybackFaceMixin:
    """Face-name overlay and per-face actions from the info panel."""

    def _hide_face_name_overlay(self, *, clear_annotations: bool) -> None:
        overlay = self._face_name_overlay
        if overlay is None:
            return
        if clear_annotations:
            overlay.clear_annotations()
        overlay.set_overlay_active(False)
    def _refresh_face_name_overlay_for_current_presentation(self) -> None:
        self._refresh_face_name_overlay_for_presentation(
            self._current_presentation
        )
    @Slot(object)
    def handle_people_snapshot_committed(self, event: object) -> None:
        presentation = self._current_presentation
        if presentation is None or not presentation.asset_id:
            return
        # Skip the refresh if the snapshot doesn't touch the current asset.
        # An absent or empty changed_asset_ids means "all assets potentially
        # changed" (e.g., a set_person_order event) — in that case always refresh.
        changed_asset_ids = getattr(event, "changed_asset_ids", None)
        if changed_asset_ids and presentation.asset_id not in changed_asset_ids:
            return
        self._refresh_face_name_overlay_for_presentation(presentation)
        self._refresh_info_panel_faces(presentation.asset_id)
    def _refresh_face_name_overlay_for_presentation(
        self,
        presentation: DetailPresentation | None,
    ) -> None:
        overlay = self._face_name_overlay
        if overlay is None:
            return
        if not self._should_show_face_name_overlay(presentation):
            self._hide_face_name_overlay(clear_annotations=True)
            return
        annotations = self._load_face_name_annotations(presentation.asset_id)
        try:
            people_service = self._people_service
            if people_service is not None:
                overlay.set_name_suggestions(people_service.list_person_name_suggestions())
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to load person name suggestions")
        overlay.set_annotations(annotations)
        overlay.set_overlay_active(bool(annotations))
    def _should_show_face_name_overlay(
        self,
        presentation: DetailPresentation | None,
    ) -> bool:
        if presentation is None or presentation.is_video or not presentation.asset_id:
            return False
        if not bool(self._show_face_names):
            return False
        if self._active_live_motion is not None:
            return False
        player_view = self._player_view
        video_area = getattr(player_view, "video_area", None)
        is_edit_mode_active = getattr(video_area, "is_edit_mode_active", None)
        if callable(is_edit_mode_active) and is_edit_mode_active():
            return False
        return True
    def _load_face_name_annotations(self, asset_id: str) -> list:
        people_service = self._people_service
        if people_service is None or not asset_id:
            return []
        try:
            return people_service.list_asset_face_annotations(asset_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to load face annotations for asset %s", asset_id)
            return []
    @Slot(str, object)
    def _handle_face_name_rename_submitted(
        self,
        person_id: str,
        new_name: object,
    ) -> None:
        if not person_id:
            return
        people_service = self._people_service
        if people_service is None:
            return
        name = new_name.strip() if isinstance(new_name, str) else None
        try:
            people_service.rename_cluster(person_id, name or None)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to rename person %s", person_id)
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = self._current_presentation
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = self._people_dashboard_refresh_callback
        if callable(refresh_callback):
            refresh_callback()
    @Slot(object)
    def _handle_info_panel_face_delete_requested(self, annotation: object) -> None:
        if not isinstance(annotation, AssetFaceAnnotation):
            return
        people_service = self._people_service
        if people_service is None:
            return
        try:
            changed = people_service.delete_face(annotation.face_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to delete face %s", annotation.face_id)
            return
        if not changed:
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = self._current_presentation
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = self._people_dashboard_refresh_callback
        if callable(refresh_callback):
            refresh_callback()
    @Slot(object, str)
    def _handle_info_panel_face_move_requested(
        self,
        annotation: object,
        target_person_id: str,
    ) -> None:
        if not isinstance(annotation, AssetFaceAnnotation) or not target_person_id:
            return
        people_service = self._people_service
        if people_service is None:
            return
        try:
            changed = people_service.move_face_to_person(annotation.face_id, target_person_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception(
                "Failed to move face %s to person %s",
                annotation.face_id,
                target_person_id,
            )
            return
        if not changed:
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = self._current_presentation
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = self._people_dashboard_refresh_callback
        if callable(refresh_callback):
            refresh_callback()
    @Slot(object, str)
    def _handle_info_panel_face_move_to_new_person_requested(
        self,
        annotation: object,
        new_name: str,
    ) -> None:
        if not isinstance(annotation, AssetFaceAnnotation):
            return
        people_service = self._people_service
        if people_service is None:
            return
        try:
            created_person_id = people_service.move_face_to_new_person(annotation.face_id, new_name)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to move face %s into a new person", annotation.face_id)
            return
        if not created_person_id:
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = self._current_presentation
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = self._people_dashboard_refresh_callback
        if callable(refresh_callback):
            refresh_callback()


__all__ = ["PlaybackFaceMixin"]
