"""Timeline and person filters for :class:`PhotoMapView`.

Both filters reduce the marker set and then hand the result to the same
``_apply_assets`` path, so they live together instead of in the view.
"""

from __future__ import annotations


class PhotoMapFilterMixin:
    """Narrow the markers by date range or by a person cluster."""

    def _on_timeline_range_changed(self, start, end) -> None:
        self._rebuild_trail()
        # Also filter map markers to only show photos within the selected
        # date range, so the map reflects the timeline selection.
        self._apply_timeline_marker_filter(start, end)
        self.request_full_update()



    def _on_timeline_granularity_changed(self, granularity: str) -> None:
        self._rebuild_trail()
        self._apply_timeline_marker_filter(
            self._timeline_slider.current_start,
            self._timeline_slider.current_end,
        )
        self.request_full_update()



    def _filter_assets_by_date(
        self, start, end, assets: list | None = None
    ) -> list:
        """Return assets whose timestamp falls within [*start*, *end*]."""
        source = assets if assets is not None else self._assets
        result: list = []
        for a in source:
            ts = None
            if a.still_image_time is not None:
                ts = a.still_image_time
            elif a.timestamp is not None:
                ts = a.timestamp
            if ts is not None and start.timestamp() <= ts <= end.timestamp():
                result.append(a)
        return result



    def _apply_timeline_marker_filter(self, start, end) -> None:
        """Update map markers to show only assets in the selected date range."""
        if not self._map_widget_built or not self._assets:
            return
        library_root = self._assets_library_root
        if library_root is None:
            return
        filtered = self._filter_assets_by_date(start, end)
        # Preserve active person filter on top of the date filter.
        if self._active_person_id and self._person_filter:
            self._person_filter.set_all_geotagged(filtered)
            filtered = self._person_filter.filter_by_person(self._active_person_id)
        self._marker_controller.set_assets(filtered, library_root)



    def show_person_filter(self, people_service) -> None:
        """Show the person filter panel on the right side of the map splitter."""
        from .person_map_panel import PersonMapPanel
        from iPhoto.application.services.person_map_filter import PersonMapFilter

        if self._person_filter_panel is not None:
            return

        self._person_filter = PersonMapFilter(people_service)
        if self._assets:
            self._person_filter.set_all_geotagged(self._assets)

        self._person_filter_panel = PersonMapPanel(people_service)
        self._person_filter_panel.personSelected.connect(self._on_person_selected)
        self._person_filter_panel.personDeselected.connect(self._on_person_deselected)
        self._person_filter_panel.setMinimumWidth(180)
        self._person_filter_panel.setMaximumWidth(360)

        splitter = self._find_map_splitter()
        if splitter is not None:
            splitter.addWidget(self._person_filter_panel)
            total = splitter.width()
            splitter.setSizes([max(total - 200, 400), 200])



    def hide_person_filter(self) -> None:
        """Hide the person filter panel."""
        if self._person_filter_panel is None:
            return
        self._person_filter_panel.personSelected.disconnect()
        self._person_filter_panel.personDeselected.disconnect()
        self._person_filter_panel.hide()
        self._person_filter_panel.setParent(None)
        self._person_filter_panel.deleteLater()
        self._person_filter_panel = None
        self._person_filter = None
        self._active_person_id = None



    def _on_person_selected(self, person_id: str) -> None:
        if self._person_filter is None:
            return
        self._active_person_id = person_id
        filtered = self._person_filter.filter_by_person(person_id)
        if self._map_widget_built:
            self._marker_controller.set_assets(filtered, self._assets_library_root)
        self._rebuild_trail()



    def _on_person_deselected(self) -> None:
        self._active_person_id = None
        if self._map_widget_built and self._assets_library_root:
            self._marker_controller.set_assets(self._assets, self._assets_library_root)
        self._rebuild_trail()

