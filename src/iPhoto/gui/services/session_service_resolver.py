"""Helpers for resolving active session-bound GUI services."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ...bootstrap.library_album_metadata_service import LibraryAlbumMetadataService
    from ...bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
    from ...bootstrap.library_asset_operation_service import LibraryAssetOperationService
    from ...bootstrap.library_asset_query_service import LibraryAssetQueryService
    from ...bootstrap.library_scan_service import LibraryScanService
    from ...library.runtime_controller import LibraryRuntimeController


def bound_album_metadata_service(
    library_manager: "LibraryRuntimeController | None",
    *,
    library_root: Path | None = None,
) -> "LibraryAlbumMetadataService | None":
    """Return the currently bound album metadata service when available."""

    return _bound_service(
        library_manager,
        attr="album_metadata_service",
        library_root=library_root,
    )


def bound_asset_lifecycle_service(
    library_manager: "LibraryRuntimeController | None",
    *,
    library_root: Path | None = None,
) -> "LibraryAssetLifecycleService | None":
    """Return the currently bound asset lifecycle service when available."""

    return _bound_service(
        library_manager,
        attr="asset_lifecycle_service",
        library_root=library_root,
    )


def bound_asset_operation_service(
    library_manager: "LibraryRuntimeController | None",
    *,
    library_root: Path | None = None,
) -> "LibraryAssetOperationService | None":
    """Return the currently bound asset operation service when available."""

    return _bound_service(
        library_manager,
        attr="asset_operation_service",
        library_root=library_root,
    )


def bound_asset_query_service(
    library_manager: "LibraryRuntimeController | None",
    *,
    library_root: Path | None = None,
) -> "LibraryAssetQueryService | None":
    """Return the currently bound asset query service when available."""

    return _bound_service(
        library_manager,
        attr="asset_query_service",
        library_root=library_root,
    )


def bound_scan_service(
    library_manager: "LibraryRuntimeController | None",
    *,
    library_root: Path | None = None,
) -> "LibraryScanService | None":
    """Return the currently bound scan service when available."""

    return _bound_service(
        library_manager,
        attr="scan_service",
        library_root=library_root,
    )


def _bound_service(
    library_manager: "LibraryRuntimeController | None",
    *,
    attr: str,
    library_root: Path | None,
):
    if library_manager is None:
        return None

    candidate = getattr(library_manager, attr, None)
    if candidate is None:
        return None

    if library_root is None:
        return candidate

    bound_root = _service_library_root(candidate)
    if bound_root is None:
        return None

    return candidate if _paths_equal(bound_root, Path(library_root)) else None


def _service_library_root(candidate: object) -> Path | None:
    library_root = getattr(candidate, "library_root", None)
    if callable(library_root):
        try:
            library_root = library_root()
        except (TypeError, OSError):
            return None

    if library_root is None:
        return None

    try:
        return Path(library_root)
    except TypeError:
        return None



def _paths_equal(left: Path, right: Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return Path(left) == Path(right)


__all__ = [
    "bound_album_metadata_service",
    "bound_asset_lifecycle_service",
    "bound_asset_operation_service",
    "bound_asset_query_service",
    "bound_scan_service",
]
