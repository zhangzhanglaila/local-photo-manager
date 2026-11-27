"""Rebind every library-scoped surface when the library tree changes.

Opening/closing a library changes the session services under the same window:
the asset runtime, gallery query surface, state service, People service and map
runtime all have to be replaced. This object owns that cascade; the coordinator
only calls :meth:`bind`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject


class LibraryTreeBinder(QObject):
    """Re-point window surfaces at the active library session."""

    def __init__(
        self,
        window,
        context,
        *,
        asset_list_vm,
        gallery_vm,
        detail_vm,
        playback,
        map_extension_download,
        library_root_getter,
        asset_query_getter,
        asset_state_getter,
        people_service_getter,
        map_runtime_getter,
        map_interaction_getter,
        is_startup_loading,
        set_playback_people_service,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._asset_list_vm = asset_list_vm
        self._gallery_vm = gallery_vm
        self._detail_vm = detail_vm
        self._playback = playback
        self._map_extension_download = map_extension_download
        self._library_root = library_root_getter
        self._asset_query_service = asset_query_getter
        self._asset_state_service = asset_state_getter
        self._people_service = people_service_getter
        self._map_runtime = map_runtime_getter
        self._map_interaction_service = map_interaction_getter
        self._is_startup_loading = is_startup_loading
        self._set_playback_people_service = set_playback_people_service

    def bind(self) -> None:
        root = self._library_root()
        self._context.asset_runtime.bind_library_root(root)
        self._asset_list_vm.rebind_asset_query_service(
            self._asset_query_service(),
            root,
        )
        asset_state_service = self._asset_state_service()
        self._gallery_vm.bind_asset_state_service(asset_state_service)
        self._detail_vm.bind_asset_state_service(asset_state_service)
        if not self._is_startup_loading():
            self._gallery_vm.on_library_tree_updated()

        ui = self._window.ui
        people_page = getattr(ui, "people_page", None)
        bound_people_service = self._people_service(library_root=root)
        if bound_people_service is not None:
            self._set_playback_people_service(bound_people_service)
        if people_page is not None:
            if bound_people_service is not None:
                people_page.set_people_service(bound_people_service)
            else:
                people_page.set_library_root(root)
            people_page.set_status_message(
                self._context.library.face_scan_status_message()
            )

        map_runtime = self._map_runtime()
        map_interaction_service = self._map_interaction_service()
        self._map_extension_download.set_package_root(
            self._resolve_map_package_root(map_runtime)
        )
        ui.map_view.set_map_runtime(map_runtime)
        ui.map_view.set_map_interaction_service(map_interaction_service)
        ui.info_panel.set_map_runtime(map_runtime)
        self._playback.set_map_runtime(map_runtime)
        if bound_people_service is not None:
            self._playback.set_people_service(bound_people_service)
        else:
            self._playback.set_people_library_root(root)

    @staticmethod
    def _resolve_map_package_root(map_runtime: object | None) -> Path:
        package_root_getter = getattr(map_runtime, "package_root", None)
        if callable(package_root_getter):
            try:
                package_root = package_root_getter()
            except Exception:
                package_root = None
            if package_root is not None:
                return Path(package_root).resolve()

        package_root = getattr(map_runtime, "_package_root", None)
        if package_root is not None:
            return Path(package_root).resolve()
        return Path(__file__).resolve().parents[3] / "maps"


__all__ = ["LibraryTreeBinder"]
