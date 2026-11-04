"""Session-owned map runtime capability adapter."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from maps.main import check_opengl_support, choose_default_map_source
from maps.map_sources import (
    MapSourceSpec,
    has_usable_osmand_native_widget,
    has_usable_osmand_search_extension,
    prefer_osmand_native_widget,
)
from maps.map_widget.native_osmand_widget import probe_native_widget_runtime

from ...application.ports import MapRuntimeCapabilities, MapRuntimePort

_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}
_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "maps"
_GAODE_CONNECTIVITY_HOSTS = (
    "webrd01.is.autonavi.com",
    "webrd02.is.autonavi.com",
)
ENV_PREFER_ONLINE_MAP = "IPHOTO_PREFER_ONLINE_MAP"


def _opengl_explicitly_disabled() -> bool:
    return os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in _TRUE_ENV_VALUES


def _has_qt_application() -> bool:
    return QGuiApplication.instance() is not None


def _prefer_online_map() -> bool:
    raw_value = os.environ.get(ENV_PREFER_ONLINE_MAP, "").strip().lower()
    if raw_value in _TRUE_ENV_VALUES:
        return True
    if raw_value in _FALSE_ENV_VALUES:
        return False
    return True


def has_usable_gaode_online_map(timeout_seconds: float = 0.35) -> bool:
    """Return whether Gaode online tiles appear reachable right now."""

    timeout = max(0.1, float(timeout_seconds))
    for host in _GAODE_CONNECTIVITY_HOSTS:
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                return True
        except OSError:
            continue
    return False


class SessionMapRuntimeService(MapRuntimePort):
    """Compute one capability snapshot shared by the active GUI session."""

    def __init__(
        self,
        package_root: Path | None = None,
        *,
        allow_native_widget_probe: bool = True,
    ) -> None:
        self._package_root = (
            Path(package_root).resolve() if package_root is not None else _MAPS_PACKAGE_ROOT
        )
        # Loading the native widget DLL can terminate the process on Windows with
        # STATUS_ENTRYPOINT_NOT_FOUND (0xc0000139) when its bundled Qt runtime
        # conflicts with the Qt build already resident in this process. That is a
        # fatal Windows exception, not a Python one, so the ``try/except`` inside
        # ``probe_native_widget_runtime`` cannot contain it. Non-GUI sessions have
        # no map surface to render and therefore must never trigger the load.
        self._allow_native_widget_probe = bool(allow_native_widget_probe)
        # Capability detection is deferred to the first query. It loads native
        # map runtimes, and that load cannot be caught: a DLL whose bundled Qt
        # conflicts with the resident one raises STATUS_ENTRYPOINT_NOT_FOUND
        # (0xc0000139) and kills the process. Constructing a session must not be
        # able to trigger that as a side effect — only code that actually needs
        # the map surface should pay for (and risk) the probe.
        self._capabilities: MapRuntimeCapabilities | None = None

    def _snapshot(self) -> MapRuntimeCapabilities:
        """Return the capability snapshot, detecting it once on first use."""

        if self._capabilities is None:
            self._capabilities = self._detect_capabilities()
        return self._capabilities

    def is_available(self) -> bool:
        return self._snapshot().display_available

    def capabilities(self) -> MapRuntimeCapabilities:
        return self._snapshot()

    def package_root(self) -> Path:
        return self._package_root

    def refresh(self) -> MapRuntimeCapabilities:
        self._capabilities = self._detect_capabilities()
        return self._capabilities

    def _detect_capabilities(self) -> MapRuntimeCapabilities:
        opengl_disabled = _opengl_explicitly_disabled()
        has_qt_app = _has_qt_application()
        python_gl_available = (
            False
            if opengl_disabled or not has_qt_app
            else check_opengl_support()
        )

        native_widget_available = False
        if (
            self._allow_native_widget_probe
            and has_qt_app
            and not opengl_disabled
            and prefer_osmand_native_widget()
            and has_usable_osmand_native_widget(self._package_root)
        ):
            native_widget_available, _ = probe_native_widget_runtime(self._package_root)

        offline_source = choose_default_map_source(
            self._package_root,
            use_opengl=python_gl_available,
            native_widget_runtime_available=native_widget_available,
        )
        online_map_available = _prefer_online_map() and has_usable_gaode_online_map()
        default_source = MapSourceSpec.gaode_standard() if online_map_available else offline_source
        osmand_extension_available = offline_source.kind == "osmand_obf"
        location_search_available = has_usable_osmand_search_extension(self._package_root)

        if default_source.kind == "gaode_standard":
            preferred_backend = "gaode_standard"
        elif osmand_extension_available and native_widget_available:
            preferred_backend = "osmand_native"
        elif osmand_extension_available:
            preferred_backend = "osmand_python"
        elif default_source.kind == "legacy_pbf":
            preferred_backend = "legacy_python"
        else:
            preferred_backend = "unavailable"

        return MapRuntimeCapabilities(
            display_available=preferred_backend != "unavailable",
            preferred_backend=preferred_backend,
            python_gl_available=python_gl_available,
            native_widget_available=native_widget_available,
            osmand_extension_available=osmand_extension_available,
            location_search_available=location_search_available,
            status_message=self._status_message(
                preferred_backend=preferred_backend,
                python_gl_available=python_gl_available,
                osmand_extension_available=osmand_extension_available,
                location_search_available=location_search_available,
            ),
        )

    @staticmethod
    def _status_message(
        *,
        preferred_backend: str,
        python_gl_available: bool,
        osmand_extension_available: bool,
        location_search_available: bool,
    ) -> str:
        if preferred_backend == "osmand_native":
            return "Native OsmAnd widget available."
        if preferred_backend == "gaode_standard":
            return "Gaode online map available."
        if preferred_backend == "osmand_python":
            if python_gl_available:
                return "OsmAnd renderer available with OpenGL."
            return "OsmAnd renderer available; Python map will use CPU fallback."
        if preferred_backend == "legacy_python":
            if osmand_extension_available:
                return "OsmAnd native runtime unavailable; using legacy Python map."
            if location_search_available:
                return "Map extension unavailable; location search remains available."
            if python_gl_available:
                return "Map extension unavailable; using legacy Python map with OpenGL."
            return "Map extension unavailable; using legacy CPU map."
        return "Map runtime unavailable."


__all__ = [
    "SessionMapRuntimeService",
    "has_usable_gaode_online_map",
]
