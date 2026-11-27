"""The Location view: focus a photo on the map, and get back to it.

Ten methods and two pieces of state used to live in ``MainCoordinator``: turning
the open detail photo into a map pin, the header back button that returns to it,
and the retry loops that wait for the lazily created GL map widget. They are one
behaviour — "explore this photo's location, then come back" — so they live here.

Nothing on the gallery hot path: it runs on a map click or a map button press.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer

from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.i18n import tr

_LOGGER = logging.getLogger(__name__)

#: The map widget is created lazily after the Location page is first shown, so
#: focusing waits for it. Values are policy, not implementation detail.
MAP_FOCUS_MAX_RETRIES = 10
MAP_FOCUS_RETRY_INTERVAL_MS = 400


class LocationCoordinator(QObject):
    """Own the Location view's "focus a photo, then return to it" behaviour."""

    #: How many times opening All Photos is retried before giving up on the row.
    _RETURN_FROM_MAP_MAX_RETRIES = 8

    def __init__(
        self,
        *,
        window,
        gallery_vm,
        gallery_store,
        navigation,
        detail_vm,
        library_getter=None,
        status_bar=None,
        people_service_getter=None,
    ) -> None:
        super().__init__()
        self._window = window
        self._gallery_vm = gallery_vm
        self._gallery_store = gallery_store
        self._navigation = navigation
        self._detail_vm = detail_vm
        self._library_getter = library_getter or (lambda: None)
        self._status_bar = status_bar
        self._people_service_getter = people_service_getter or (lambda: None)
        #: The photo the user came from, so "back" can reopen it.
        self._return_from_map_path: Path | None = None
        self._header_back_btn = None

    # ------------------------------------------------------------------ entry
    def toggle_detail_map(self) -> None:
        """Navigate to the Location view focused on the current photo."""

        presentation = self._detail_vm.presentation.value
        if presentation is None:
            return
        gps = presentation.info.get("gps")
        if not isinstance(gps, dict):
            return
        lat = gps.get("lat")
        lon = gps.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return
        rel = presentation.info.get("rel")
        if not isinstance(rel, str) or not rel:
            rel = presentation.path.name
        asset = GeotaggedAsset(
            library_relative=rel,
            album_relative=Path(rel).name,
            absolute_path=presentation.path,
            album_path=presentation.path.parent,
            asset_id=presentation.asset_id or rel,
            latitude=float(lat),
            longitude=float(lon),
            is_image=not presentation.is_video,
            is_video=bool(presentation.is_video),
            still_image_time=None,
            timestamp=None,
            duration=None,
            location_name=presentation.location,
            live_photo_group_id=None,
            live_partner_rel=(
                presentation.live_motion_rel.as_posix()
                if presentation.live_motion_rel is not None
                else None
            ),
        )
        # Remember the current photo so the header back button can return to it.
        self._return_from_map_path = presentation.path
        self._gallery_vm.open_location_map_focused(asset)
        self._window.ui.map_view.set_focused_asset_mode(True)
        # Show back button in header immediately.
        self._add_header_back_button()
        # Highlight sidebar immediately.
        try:
            self._window.ui.sidebar.select_static_node("Location")
            self._window.ui.sidebar._tree.viewport().update()
        except Exception:
            pass
        # Center map on photo location — retry quickly until native GL widget is ready.
        QTimer.singleShot(0, lambda: self._try_focus_map(lat, lon, 0))

    def show_all(self) -> None:
        """Show every geotagged asset, forgetting the photo we came from."""

        # Clear return path so we don't jump back to the previous photo
        self._return_from_map_path = None
        # Hide the header back button if it exists
        if self._header_back_btn is not None:
            self._header_back_btn.hide()
        self._gallery_vm.show_all_location_assets()
        self._window.ui.map_view.set_focused_asset_mode(False)

    def back_clicked(self) -> None:
        """Header back button: return to the originating photo."""

        if self._header_back_btn is not None:
            self._header_back_btn.hide()
        if self._return_from_map_path is not None:
            self.return_from_map()
        else:
            self._navigation.open_all_photos()

    def sidebar_return(self, name: str) -> None:
        """Clicking "Location" while already there returns to the photo instead."""

        if name != "Location":
            return
        path = self._return_from_map_path
        if path is None:
            return
        self._return_from_map_path = None
        # Tell NavigationCoordinator to skip re-navigating to Location.
        self._navigation._pending_detail_return = path
        # Navigate to All Photos first, then open the photo detail.
        self._navigation.open_all_photos()
        QTimer.singleShot(50, lambda: self._try_return_to_photo(path, 0))

    def return_from_map(self) -> None:
        """Clicking All Photos after exploring from detail reopens the photo."""

        path = self._return_from_map_path
        if path is None:
            return
        self._return_from_map_path = None
        if self._header_back_btn is not None:
            self._header_back_btn.hide()
        QTimer.singleShot(100, lambda: self._try_return_to_photo(path, 0))

    def toggle_person_filter(self, checked: bool) -> None:
        """Show or hide the person filter panel over the map."""

        ui = self._window.ui
        if checked:
            people_service = self._people_service_getter()
            if people_service is None:
                try:
                    from iPhoto.people.service import PeopleService

                    people_service = PeopleService()
                except Exception:
                    ui.map_view.btn_person_filter.setChecked(False)
                    return
            ui.map_view.show_person_filter(people_service)
        else:
            ui.map_view.hide_person_filter()

    def toggle_trail(self, checked: bool) -> None:
        """Build and show the day-granularity trail, or hide it."""

        ui = self._window.ui
        status = self._status_bar
        if not checked:
            ui.map_view.clear_trail()
            if status is not None:
                status.show_message(tr("map.trail.hidden"), 2000)
            return

        library = self._library_getter()
        if library is None:
            ui.map_view.btn_trail_toggle.setChecked(False)
            if status is not None:
                status.show_message(tr("map.trail.unavailable"), 4000)
            return
        try:
            from iPhoto.application.services.trail_service import TrailService

            geotagged = library.get_geotagged_assets()
            if not geotagged:
                ui.map_view.btn_trail_toggle.setChecked(False)
                if status is not None:
                    status.show_message(tr("map.trail.no_gps"), 5000)
                return
            trail_service = TrailService()
            trail = trail_service.build_trail(geotagged, granularity="day")
            if trail.is_empty:
                ui.map_view.btn_trail_toggle.setChecked(False)
                if status is not None:
                    status.show_message(tr("map.trail.empty"), 5000)
                return
            ui.map_view.set_trail(trail)
            ui.map_view._trail_service = trail_service
            ui.map_view._trail_geotagged = geotagged
            if status is not None:
                status.show_message(
                    tr(
                        "map.trail.summary",
                        photos=trail.total_photos,
                        segments=len(trail.segments),
                    ),
                    4000,
                )
        except Exception as exc:
            _LOGGER.warning("Failed to build trail: %s", exc)
            ui.map_view.btn_trail_toggle.setChecked(False)
            if status is not None:
                status.show_message(tr("map.trail.failed", message=exc), 5000)

    # --------------------------------------------------------------- internals
    def _add_header_back_button(self) -> None:
        """Add the "return to photo" button to the main header."""

        ui = self._window.ui
        btn = self._header_back_btn
        if btn is not None:
            btn.show()
            return
        from PySide6.QtWidgets import QPushButton

        btn = QPushButton("← Photo", ui.main_header)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("Return to photo")
        btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 4px 10px; "
            "background: rgba(0,120,215,180); color: white; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: rgba(0,120,215,240); }"
        )
        btn.clicked.connect(self.back_clicked)
        header_layout = ui.main_header.layout()
        if header_layout is not None:
            header_layout.addWidget(btn)
        self._header_back_btn = btn

    def _try_focus_map(self, lat: float, lon: float, attempt: int) -> None:
        """Center the Location map on the given coordinates at high zoom."""

        ui = self._window.ui
        try:
            map_widget = ui.map_view.map_widget()
        except RuntimeError:
            self._retry_focus(lat, lon, attempt)
            return
        if map_widget is None:
            self._retry_focus(lat, lon, attempt)
            return
        try:
            map_widget.set_zoom(17.0)
            map_widget.center_on(lon, lat)
        except Exception:
            _LOGGER.debug(
                "Could not focus the map yet (attempt %s)", attempt, exc_info=True
            )
            self._retry_focus(lat, lon, attempt)

    def _retry_focus(self, lat: float, lon: float, attempt: int) -> None:
        if attempt < MAP_FOCUS_MAX_RETRIES:
            QTimer.singleShot(
                MAP_FOCUS_RETRY_INTERVAL_MS,
                lambda: self._try_focus_map(lat, lon, attempt + 1),
            )

    def _try_return_to_photo(self, path: Path, attempt: int) -> None:
        row = self._gallery_store.row_for_path(path)
        if row is not None:
            self._gallery_vm.open_row(row)
            return
        if attempt < self._RETURN_FROM_MAP_MAX_RETRIES:
            QTimer.singleShot(
                100 * (attempt + 1),
                lambda: self._try_return_to_photo(path, attempt + 1),
            )


__all__ = [
    "LocationCoordinator",
    "MAP_FOCUS_MAX_RETRIES",
    "MAP_FOCUS_RETRY_INTERVAL_MS",
]
