"""Info panel content for the detail view: metadata, faces, manual faces.

Also split out of ``PlaybackCoordinator``. Everything here updates the info panel
for the current presentation: cached metadata, background enrichment, the face
list, and adding a manual face. Bodies moved verbatim.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Slot

from ...i18n import tr

from iPhoto.people.repository import AssetFaceAnnotation

from ..ui.tasks.info_panel_metadata_worker import (
    InfoPanelMetadataResult,
    InfoPanelMetadataWorker,
)
from ..ui.tasks.manual_face_add_worker import ManualFaceAddWorker
from ..ui.widgets import dialogs
from .playback_constants import _INFO_PANEL_METADATA_CACHE_MAX, _LOCATION_EXTENSION_PROMPT

LOGGER = logging.getLogger(__name__)


class PlaybackInfoMixin:
    """Info panel metadata, face list and manual face entry."""

    def _refresh_info_panel(self, info: dict) -> None:
        if not self._info_panel:
            return
        self._ensure_info_panel_metadata_state()
        capabilities = self._map_runtime_capabilities()
        location_enabled = self._refresh_location_extension_state()
        self._info_panel.set_location_capability(
            enabled=location_enabled,
            preview_enabled=self._info_panel_preview_enabled(capabilities, location_enabled=location_enabled),
            fallback_text=tr(_LOCATION_EXTENSION_PROMPT),
        )
        local_info = dict(info)
        abs_path = local_info.get("abs")
        path_key = self._info_panel_path_key(abs_path)
        if path_key is not None:
            cached = self._info_panel_metadata_cache.get(path_key)
            if cached:
                local_info = self._merge_info_panel_metadata(local_info, cached)
        current_path = Path(path_key) if path_key is not None else None
        location_preview_path = self._location_preview_path
        location_preview_metadata = self._location_preview_metadata
        if (
            current_path is not None
            and location_preview_path is not None
            and location_preview_metadata is not None
            and current_path == location_preview_path
        ):
            local_info = self._merge_info_panel_metadata(local_info, location_preview_metadata)
        needs_enrichment = self._info_panel_metadata_needs_enrichment(local_info)
        should_queue_enrichment = bool(
            path_key is not None
            and needs_enrichment
            and path_key not in self._info_panel_metadata_attempted
            and path_key not in self._info_panel_metadata_inflight
        )
        is_loading = bool(
            path_key is not None
            and needs_enrichment
            and (
                should_queue_enrichment
                or path_key in self._info_panel_metadata_inflight
            )
        )
        if is_loading:
            local_info["_metadata_loading"] = True
        else:
            local_info.pop("_metadata_loading", None)
        self._info_panel.set_asset_metadata(local_info)
        location_assign_path = self._location_assign_path
        self._info_panel.set_location_busy(
            bool(self._location_assign_inflight)
            and location_assign_path is not None
            and current_path == location_assign_path
        )
        presentation = self._current_presentation
        self._refresh_info_panel_faces(presentation.asset_id if presentation is not None else None)
        if should_queue_enrichment:
            self._queue_info_panel_metadata_enrichment(
                Path(path_key),
                is_video=bool(local_info.get("is_video")),
            )
        # Refresh AI metadata (caption and tags)
        self._refresh_info_panel_ai_metadata(presentation.asset_id if presentation is not None else None)
    def _refresh_info_panel_ai_metadata(self, asset_id: str | None) -> None:
        """Refresh AI-generated metadata (caption and tags) in the info panel."""
        info_panel = self._info_panel
        if info_panel is None or asset_id is None:
            return

        if not self._settings.get("agent.semantic_search_enabled", False):
            info_panel.set_asset_ai_metadata(caption=None, tags=None)
            return

        # The session only exists once an album is open; ask for it on demand.
        session = (
            self._library_session_getter() if self._library_session_getter else None
        )
        if session is None:
            return
        embedding_repo = session.get_embedding_repository()
        if embedding_repo is None:
            return

        # Fetch AI metadata in background
        from PySide6.QtCore import QRunnable, Slot, QThreadPool
        import logging

        class AIMetadataWorker(QRunnable):
            def __init__(self, repo, aid, callback):
                super().__init__()
                self.setAutoDelete(True)
                self._repo = repo
                self._asset_id = aid
                self._callback = callback

            @Slot()
            def run(self):
                try:
                    caption = self._repo.get_caption(self._asset_id)
                    tags = self._repo.get_tags(self._asset_id)
                    self._callback(caption, tags)
                except Exception as e:
                    logging.getLogger(__name__).debug("Failed to fetch AI metadata: %s", e)
                    self._callback(None, None)

        def on_ai_metadata_ready(caption, tags):
            self.aiMetadataReady.emit(asset_id, caption, tags)

        worker = AIMetadataWorker(embedding_repo, asset_id, on_ai_metadata_ready)
        QThreadPool.globalInstance().start(worker)
    def _apply_info_panel_ai_metadata(
        self, asset_id: str, caption: object, tags: object
    ) -> None:
        """Show AI metadata, but only if the user is still looking at *asset_id*."""

        presentation = self._current_presentation
        if presentation is None or presentation.asset_id != asset_id:
            return
        info_panel = self._info_panel
        if info_panel is None:
            return
        info_panel.set_asset_ai_metadata(caption=caption, tags=tags)
    def _refresh_info_panel_faces(self, asset_id: str | None) -> None:
        info_panel = self._info_panel
        if info_panel is None:
            return
        people_service = self._people_service
        if people_service is not None:
            try:
                info_panel.set_face_action_candidates(
                    people_service.list_clusters(include_hidden=True)
                )
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to load face action candidates")
                info_panel.set_face_action_candidates([])
        if not asset_id:
            info_panel.set_asset_faces([])
            return
        info_panel.set_asset_faces(self._compose_info_panel_faces(asset_id))
    def _compose_info_panel_faces(self, asset_id: str) -> list[AssetFaceAnnotation]:
        annotations = list(self._load_face_name_annotations(asset_id))
        pending = self._pending_manual_face_annotations.get(asset_id, [])
        if pending:
            annotations.extend(pending)
        return annotations
    def _queue_pending_manual_face(
        self,
        asset_id: str,
        presentation: DetailPresentation,
        payload: dict[str, object],
    ) -> None:
        requested_box = payload.get("requested_box")
        if (
            not isinstance(requested_box, tuple)
            or len(requested_box) != 4
            or not all(isinstance(value, int) for value in requested_box)
        ):
            return
        pending_faces = self._pending_manual_face_annotations
        if not isinstance(pending_faces, dict):
            pending_faces = {}
            self._pending_manual_face_annotations = pending_faces
        sequence = int(self._pending_manual_face_sequence) + 1
        self._pending_manual_face_sequence = sequence
        name = payload.get("name")
        person_id = payload.get("person_id")
        image_width = presentation.info.get("w")
        image_height = presentation.info.get("h")
        pending_face = AssetFaceAnnotation(
            face_id=f"pending-manual-{sequence}",
            person_id=person_id if isinstance(person_id, str) and person_id else None,
            display_name=name.strip() if isinstance(name, str) and name.strip() else None,
            box_x=requested_box[0],
            box_y=requested_box[1],
            box_w=requested_box[2],
            box_h=requested_box[3],
            image_width=image_width if isinstance(image_width, int) and image_width > 0 else max(1, requested_box[0] + requested_box[2]),
            image_height=image_height if isinstance(image_height, int) and image_height > 0 else max(1, requested_box[1] + requested_box[3]),
            thumbnail_path=None,
            is_manual=True,
        )
        pending_faces.setdefault(asset_id, []).append(pending_face)
    def _clear_pending_manual_faces(self, asset_id: str | None) -> None:
        if not asset_id:
            return
        pending_faces = self._pending_manual_face_annotations
        if isinstance(pending_faces, dict):
            pending_faces.pop(asset_id, None)
    def toggle_info_panel(self) -> None:
        self._detail_vm.toggle_info()
    @Slot()
    def _handle_info_panel_dismissed(self) -> None:
        self._detail_vm.hide_info_panel(refresh_presentation=False)
    def _ensure_info_panel_metadata_state(self) -> None:
        if not hasattr(self, "_info_panel_metadata_cache"):
            self._info_panel_metadata_cache = {}
        if not hasattr(self, "_info_panel_metadata_inflight"):
            self._info_panel_metadata_inflight = set()
        if not hasattr(self, "_info_panel_metadata_attempted"):
            self._info_panel_metadata_attempted = set()
    def _clear_info_panel_metadata_state(self) -> None:
        self._ensure_info_panel_metadata_state()
        self._info_panel_metadata_cache.clear()
        self._info_panel_metadata_inflight.clear()
        self._info_panel_metadata_attempted.clear()
    def _info_panel_path_key(self, path: object) -> str | None:
        if isinstance(path, Path):
            return str(path)
        if isinstance(path, str) and path.strip():
            return str(Path(path))
        return None
    def _info_panel_metadata_needs_enrichment(self, info: dict[str, Any]) -> bool:
        is_video = bool(info.get("is_video"))
        return (
            (not info.get("frame_rate") or not info.get("lens"))
            if is_video
            else not info.get("iso")
        )
    def _merge_info_panel_metadata(
        self,
        base_info: dict[str, Any],
        extra_info: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base_info)
        merged.update({key: value for key, value in extra_info.items() if value is not None})
        merged.pop("_metadata_loading", None)
        return merged
    def _queue_info_panel_metadata_enrichment(self, path: Path, *, is_video: bool) -> None:
        self._ensure_info_panel_metadata_state()
        path_key = str(path)
        if path_key in self._info_panel_metadata_inflight:
            return
        self._info_panel_metadata_inflight.add(path_key)

        worker = InfoPanelMetadataWorker(path, is_video=is_video)
        worker.signals.ready.connect(self._handle_info_panel_metadata_ready)
        worker.signals.error.connect(self._handle_info_panel_metadata_error)
        worker.signals.finished.connect(self._handle_info_panel_metadata_finished)
        try:
            QThreadPool.globalInstance().start(worker, -1)
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to start metadata enrichment worker for %s", path_key, exc_info=True)
            self._info_panel_metadata_inflight.discard(path_key)
            self._info_panel_metadata_attempted.discard(path_key)
    @Slot(object)
    def _handle_info_panel_metadata_ready(self, result: InfoPanelMetadataResult) -> None:
        self._ensure_info_panel_metadata_state()
        path_key = str(result.path)
        # Evict oldest entry (insertion-order FIFO, Python 3.7+) before inserting
        # so the cache never grows beyond _INFO_PANEL_METADATA_CACHE_MAX entries.
        if len(self._info_panel_metadata_cache) >= _INFO_PANEL_METADATA_CACHE_MAX:
            evict_key = next(iter(self._info_panel_metadata_cache))
            del self._info_panel_metadata_cache[evict_key]
            self._info_panel_metadata_attempted.discard(evict_key)
        self._info_panel_metadata_cache[path_key] = dict(result.metadata)

        if not self._info_panel or not self._info_panel.isVisible():
            return
        presentation = self._current_presentation
        if presentation is None or presentation.path != result.path:
            return
        local_info = self._merge_info_panel_metadata(presentation.info, result.metadata)
        self._info_panel.set_asset_metadata(local_info)
        self._refresh_info_panel_faces(presentation.asset_id)
    @Slot(str, str)
    def _handle_info_panel_metadata_error(self, path_key: str, message: str) -> None:
        LOGGER.debug(
            "Failed to enrich info-panel metadata for %s: %s",
            path_key,
            message,
        )
    @Slot(str)
    def _handle_info_panel_metadata_finished(self, path_key: str) -> None:
        self._ensure_info_panel_metadata_state()
        self._info_panel_metadata_inflight.discard(path_key)
        self._info_panel_metadata_attempted.add(path_key)
    @Slot()
    def _handle_manual_face_add_requested(self) -> None:
        presentation = self._current_presentation
        overlay = self._face_name_overlay
        if overlay is None or presentation is None or presentation.is_video or not presentation.asset_id:
            return
        try:
            overlay.set_name_suggestions(self._people_service.list_person_name_suggestions())
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to load person name suggestions")
        overlay.set_annotations(self._load_face_name_annotations(presentation.asset_id))
        overlay.set_overlay_active(True)
        overlay.start_manual_face()
    @Slot(object)
    def _handle_manual_face_submitted(self, payload: object) -> None:
        if self._manual_face_add_inflight:
            return
        presentation = self._current_presentation
        overlay = self._face_name_overlay
        library_root = self._people_service.library_root()
        if (
            presentation is None
            or overlay is None
            or library_root is None
            or not presentation.asset_id
            or not isinstance(payload, dict)
        ):
            return
        requested_box = payload.get("requested_box")
        if (
            not isinstance(requested_box, tuple)
            or len(requested_box) != 4
            or not all(isinstance(value, int) for value in requested_box)
        ):
            overlay.show_manual_error("The face circle could not be mapped back to the photo.")
            return
        self._manual_face_add_inflight = True
        self._manual_face_inflight_asset_id = presentation.asset_id
        overlay.set_manual_face_busy(True)
        self._queue_pending_manual_face(presentation.asset_id, presentation, payload)
        self._refresh_info_panel_faces(presentation.asset_id)
        worker = ManualFaceAddWorker(
            library_root=library_root,
            asset_id=presentation.asset_id,
            requested_box=requested_box,
            name_or_none=payload.get("name") if isinstance(payload.get("name"), str) else None,
            person_id=payload.get("person_id") if isinstance(payload.get("person_id"), str) else None,
            people_service=self._people_service,
        )
        worker.signals.ready.connect(self._handle_manual_face_ready)
        worker.signals.error.connect(self._handle_manual_face_error)
        worker.signals.finished.connect(self._handle_manual_face_finished)
        QThreadPool.globalInstance().start(worker, -1)
    @Slot(object)
    def _handle_manual_face_ready(self, result: object) -> None:
        submitted_asset_id = self._manual_face_inflight_asset_id
        if submitted_asset_id:
            self._clear_pending_manual_faces(submitted_asset_id)
        presentation = self._current_presentation
        if presentation is not None and presentation.asset_id == submitted_asset_id:
            self._refresh_face_name_overlay_for_current_presentation()
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = self._people_dashboard_refresh_callback
        if callable(refresh_callback):
            refresh_callback()
    @Slot(str)
    def _handle_manual_face_error(self, message: str) -> None:
        submitted_asset_id = self._manual_face_inflight_asset_id
        if not submitted_asset_id:
            presentation = self._current_presentation
            submitted_asset_id = presentation.asset_id if presentation is not None else None
        if submitted_asset_id:
            self._clear_pending_manual_faces(submitted_asset_id)
        presentation = self._current_presentation
        if (
            submitted_asset_id
            and presentation is not None
            and presentation.asset_id == submitted_asset_id
        ):
            self._refresh_info_panel_faces(submitted_asset_id)
        overlay = self._face_name_overlay
        if overlay is not None:
            overlay.set_manual_face_busy(False)
            overlay.show_manual_error(message)
    @Slot()
    def _handle_manual_face_finished(self) -> None:
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id = None
        overlay = self._face_name_overlay
        if overlay is not None:
            overlay.set_manual_face_busy(False)


__all__ = ["PlaybackInfoMixin"]
