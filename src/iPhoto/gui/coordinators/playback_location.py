"""Location assignment for the detail view: search, pick, write GPS.

These 22 methods used to be part of ``PlaybackCoordinator`` (1890 lines). They
cover the info panel's location field: query the geocoder, preview suggestions,
assign the chosen place through ExifTool, and warn when the tool is missing or
the file cannot be written. Bodies moved verbatim.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QLocale, QThreadPool, QTimer, Slot

from ...i18n import tr

from iPhoto.infrastructure.services.map_runtime_service import SessionMapRuntimeService
from maps.osmand_search import OsmAndSearchService, SearchSuggestion

from ..ui.tasks.assign_location_worker import AssignLocationRequest, AssignLocationWorker
from ..ui.widgets import dialogs
from .playback_constants import (
    _INFO_PANEL_METADATA_CACHE_MAX,
    _LOCATION_EXTENSION_PROMPT,
    _LOCATION_EXIFTOOL_LIMITED_MESSAGE,
    _LOCATION_EXIFTOOL_LIMITED_TITLE,
    _LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE,
    _LOCATION_FILE_WRITE_LIMITED_TITLE,
    _LOCATION_SEARCH_RESULT_LIMIT,
)

LOGGER = logging.getLogger(__name__)


class PlaybackLocationMixin:
    """Info-panel location search and GPS assignment."""

    def _update_detail_map(self, info: dict) -> None:
        """Update the detail map panel with GPS coordinates from the current asset."""
        if self._detail_map_panel is None:
            return
        gps = info.get("gps")
        if isinstance(gps, dict):
            lat = gps.get("lat")
            lon = gps.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                self._detail_map_panel.set_location(float(lat), float(lon))
                return
        self._detail_map_panel.clear_location()
    def _refresh_location_extension_state(self) -> bool:
        enabled = False
        capabilities = self._map_runtime_capabilities()
        if capabilities is not None:
            enabled = bool(capabilities.location_search_available)
        if not enabled:
            self._reset_location_search_service()
            return False

        if self._location_search_service is not None:
            return True

        try:
            self._location_search_service = OsmAndSearchService(
                package_root=self._map_runtime_package_root(),
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to initialize offline location search", exc_info=True)
            self._location_search_service = None
            return False
        return True
    @staticmethod
    def _info_panel_preview_enabled(
        capabilities,
        *,
        location_enabled: bool = False,
    ) -> bool:
        if capabilities is None:
            return bool(location_enabled)
        return bool(
            getattr(capabilities, "display_available", False)
            and getattr(capabilities, "osmand_extension_available", False)
        )
    def _map_runtime_capabilities(self):
        map_runtime = self._ensure_map_runtime()
        capabilities_getter = getattr(map_runtime, "capabilities", None)
        if callable(capabilities_getter):
            return capabilities_getter()
        return None
    def _map_runtime_package_root(self) -> Path | None:
        map_runtime = self._ensure_map_runtime()
        package_root_getter = getattr(map_runtime, "package_root", None)
        if callable(package_root_getter):
            try:
                package_root = package_root_getter()
            except Exception:
                LOGGER.debug("Failed to resolve playback map runtime package root", exc_info=True)
            else:
                if package_root is not None:
                    return Path(package_root)
        package_root = getattr(map_runtime, "_package_root", None)
        if package_root is not None:
            return Path(package_root)
        return None
    def _ensure_map_runtime(self) -> MapRuntimePort | None:
        map_runtime = self._map_runtime
        if map_runtime is not None:
            return map_runtime
        library_manager = self._library_manager
        library_runtime = getattr(library_manager, "map_runtime", None)
        if library_runtime is not None:
            self._map_runtime = library_runtime
            return library_runtime
        try:
            fallback_runtime = SessionMapRuntimeService()
        except Exception:
            LOGGER.debug("Failed to create fallback session map runtime", exc_info=True)
            return None
        self._map_runtime = fallback_runtime
        return fallback_runtime
    def _reset_location_search_service(self) -> None:
        location_timer = self._location_search_timer
        if location_timer is not None:
            location_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        location_cache = self._location_search_cache
        if location_cache is not None:
            location_cache.clear()
        location_service = self._location_search_service
        if location_service is not None:
            location_service.shutdown()
            self._location_search_service = None
    def _normalize_location_query(self, query: str) -> str:
        return " ".join(query.split()).casefold()
    def _should_search_location_query(self, query: str) -> bool:
        trimmed = " ".join(query.split())
        if not trimmed:
            return False
        if len(trimmed) >= 2:
            return True
        return any(ord(character) >= 128 for character in trimmed)
    def _preview_cached_location_suggestions(
        self,
        query: str,
    ) -> tuple[list[SearchSuggestion] | None, bool]:
        normalized_query = self._normalize_location_query(query)
        if not normalized_query:
            return None, False
        exact = self._location_search_cache.get(normalized_query)
        if exact is not None:
            return list(exact), True

        for cached_query, cached_results in sorted(
            self._location_search_cache.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if not normalized_query.startswith(cached_query):
                continue
            filtered = [
                suggestion
                for suggestion in cached_results
                if normalized_query in self._normalize_location_query(
                    " ".join(
                        part
                        for part in (
                            suggestion.display_name,
                            suggestion.secondary_text,
                        )
                        if part
                    )
                )
            ]
            if filtered:
                return filtered[:_LOCATION_SEARCH_RESULT_LIMIT], False
        return None, False
    @Slot(str)
    def _handle_location_query_changed(self, query: str) -> None:
        info_panel = self._info_panel
        if info_panel is None:
            return

        self._location_search_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        if self._location_search_service is not None:
            self._location_search_service.abort()

        if not self._refresh_location_extension_state():
            info_panel.set_location_suggestions([])
            return
        if self._location_assign_inflight:
            info_panel.set_location_suggestions([])
            return
        if not self._should_search_location_query(query):
            info_panel.set_location_suggestions([])
            return

        preview, is_exact = self._preview_cached_location_suggestions(query)
        if preview is not None:
            info_panel.set_location_suggestions(preview)
            if is_exact:
                return
        else:
            info_panel.set_location_suggestions([])

        presentation = self._current_presentation
        if presentation is None:
            return
        self._pending_location_query = query.strip()
        self._location_search_target_path = presentation.path
        self._location_search_timer.start()
    @Slot()
    def _perform_location_search(self) -> None:
        info_panel = self._info_panel
        presentation = self._current_presentation
        query = self._pending_location_query.strip()
        target_path = self._location_search_target_path
        self._pending_location_query = ""
        if (
            info_panel is None
            or presentation is None
            or not query
            or target_path is None
            or presentation.path != target_path
        ):
            return

        if not self._refresh_location_extension_state():
            info_panel.set_location_suggestions([])
            return
        service = self._location_search_service
        if service is None:
            info_panel.set_location_suggestions([])
            return

        locale = QLocale.system().bcp47Name()
        try:
            suggestions = service.search(
                query,
                limit=_LOCATION_SEARCH_RESULT_LIMIT,
                locale=locale,
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning("Offline location search failed for query %r", query, exc_info=True)
            suggestions = []

        normalized_query = self._normalize_location_query(query)
        self._location_search_cache[normalized_query] = list(suggestions)
        if len(self._location_search_cache) > 64:
            oldest_key = next(iter(self._location_search_cache))
            self._location_search_cache.pop(oldest_key, None)

        current_presentation = self._current_presentation
        if (
            current_presentation is None
            or current_presentation.path != target_path
            or self._location_assign_inflight
        ):
            return
        info_panel.set_location_suggestions(suggestions)
    @Slot(str, object)
    def _handle_location_confirm_requested(self, query: str, suggestion_obj: object) -> None:
        if self._location_assign_inflight or not self._refresh_location_extension_state():
            return
        if not isinstance(suggestion_obj, SearchSuggestion):
            return
        presentation = self._current_presentation
        if presentation is None:
            return

        rel_value = presentation.info.get("rel")
        if not isinstance(rel_value, str) or not rel_value.strip():
            return

        library_root = None
        library_manager = self._library_manager
        if library_manager is not None:
            library_root = library_manager.root()
        if library_root is None:
            library_root = self._asset_model.store.library_root()
        if library_root is None:
            return

        self._location_search_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        if self._location_search_service is not None:
            self._location_search_service.abort()

        display_name = suggestion_obj.display_name.strip() or query.strip()
        self._location_assign_inflight = True
        self._location_assign_path = presentation.path
        self._location_preview_path = presentation.path
        self._location_preview_metadata = {
            "location": display_name,
            "place": display_name,
            "gps": {
                "lat": float(suggestion_obj.latitude),
                "lon": float(suggestion_obj.longitude),
            },
        }
        if self._info_panel is not None:
            self._info_panel.preview_location(
                display_name,
                float(suggestion_obj.latitude),
                float(suggestion_obj.longitude),
            )
            self._info_panel.set_location_busy(True)
            self._info_panel.set_location_suggestions([])

        existing_metadata = self._asset_model.metadata_for_path(presentation.path) or dict(presentation.info)
        worker = AssignLocationWorker(
            AssignLocationRequest(
                library_root=Path(library_root),
                asset_path=presentation.path,
                asset_rel=rel_value,
                display_name=display_name,
                latitude=float(suggestion_obj.latitude),
                longitude=float(suggestion_obj.longitude),
                is_video=bool(presentation.is_video),
                existing_metadata=existing_metadata,
            )
        )
        worker.signals.ready.connect(self._handle_location_assignment_ready)
        worker.signals.error.connect(self._handle_location_assignment_error)
        worker.signals.finished.connect(self._handle_location_assignment_finished)
        try:
            QThreadPool.globalInstance().start(worker, -1)
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to start location assignment worker", exc_info=True)
            self._location_assign_inflight = False
            self._location_assign_path = None
            if self._info_panel is not None:
                self._info_panel.set_location_busy(False)
    @Slot(object)
    def _handle_location_assignment_ready(self, result: object) -> None:
        asset_path = getattr(result, "asset_path", None)
        metadata = getattr(result, "metadata", None)
        if not isinstance(asset_path, Path) or not isinstance(metadata, dict):
            return

        file_write_error = getattr(result, "file_write_error", None)
        if isinstance(file_write_error, str) and file_write_error.strip():
            LOGGER.warning(
                "Location saved in the library, but GPS metadata was not written to %s: %s",
                asset_path,
                file_write_error,
            )
            if self._is_missing_exiftool_error(file_write_error):
                self._queue_location_exiftool_missing_warning()
            else:
                self._queue_location_file_write_warning(file_write_error)

        row = self._asset_model.row_for_path(asset_path)
        if row is not None:
            self._asset_model.store.update_asset_metadata(row, dict(metadata))

        path_key = str(asset_path)
        if len(self._info_panel_metadata_cache) >= _INFO_PANEL_METADATA_CACHE_MAX:
            evict_key = next(iter(self._info_panel_metadata_cache))
            del self._info_panel_metadata_cache[evict_key]
            self._info_panel_metadata_attempted.discard(evict_key)
        self._info_panel_metadata_cache[path_key] = dict(metadata)
        self._info_panel_metadata_attempted.add(path_key)
        self._info_panel_metadata_inflight.discard(path_key)
        if self._location_preview_path == asset_path:
            self._location_preview_path = None
            self._location_preview_metadata = None

        self._detail_vm.refresh_current()

        library_manager = self._library_manager
        invalidate = getattr(library_manager, "invalidate_geotagged_assets_cache", None)
        if callable(invalidate):
            try:
                invalidate(emit_tree_updated=False)
            except Exception:  # noqa: BLE001
                LOGGER.warning("Failed to refresh geotagged asset caches", exc_info=True)
        invalidate_location_session = self._location_session_invalidator
        if callable(invalidate_location_session):
            try:
                invalidate_location_session()
            except Exception:  # noqa: BLE001
                LOGGER.warning("Failed to invalidate cached location-session data", exc_info=True)
    def _is_missing_exiftool_error(self, message: str) -> bool:
        normalized = message.casefold()
        return "exiftool" in normalized and (
            "not found" in normalized or "filenotfounderror" in normalized
        )
    def _queue_location_exiftool_missing_warning(self) -> None:
        QTimer.singleShot(0, self._show_location_exiftool_missing_warning)
    def _queue_location_file_write_warning(self, message: str) -> None:
        QTimer.singleShot(0, lambda: self._show_location_file_write_warning(message))
    def _location_warning_parent(self) -> QWidget | None:
        info_panel = self._info_panel
        if info_panel is None:
            return None
        parent_widget = info_panel.parentWidget()
        return parent_widget if parent_widget is not None else info_panel
    def _show_location_exiftool_missing_warning(self) -> None:
        popup_parent = self._location_warning_parent()
        if popup_parent is None:
            return
        dialogs.show_warning(
            popup_parent,
            tr(_LOCATION_EXIFTOOL_LIMITED_MESSAGE),
            title=tr(_LOCATION_EXIFTOOL_LIMITED_TITLE),
        )
    def _show_location_file_write_warning(self, message: str) -> None:
        popup_parent = self._location_warning_parent()
        if popup_parent is None:
            return
        dialogs.show_warning(
            popup_parent,
            tr(_LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE, reason=message.strip()),
            title=tr(_LOCATION_FILE_WRITE_LIMITED_TITLE),
        )
    @Slot(str)
    def _handle_location_assignment_error(self, message: str) -> None:
        LOGGER.warning("Failed to assign location: %s", message)
        if self._location_preview_path == self._location_assign_path:
            self._location_preview_path = None
            self._location_preview_metadata = None
        info_panel = self._info_panel
        presentation = self._current_presentation
        if (
            info_panel is not None
            and presentation is not None
            and info_panel.isVisible()
            and self._location_assign_path is not None
            and presentation.path == self._location_assign_path
        ):
            self._refresh_info_panel(presentation.info)
        elif info_panel is not None:
            info_panel.set_location_busy(False)
    @Slot()
    def _handle_location_assignment_finished(self) -> None:
        self._location_assign_inflight = False
        self._location_assign_path = None
        info_panel = self._info_panel
        presentation = self._current_presentation
        if info_panel is None:
            return
        if presentation is not None and info_panel.isVisible():
            self._refresh_info_panel(presentation.info)
            return
        info_panel.set_location_busy(False)


__all__ = ["PlaybackLocationMixin"]
