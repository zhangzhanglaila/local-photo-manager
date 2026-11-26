"""Runtime-owned asset repository and thumbnail service rebinding."""

from __future__ import annotations

from pathlib import Path

from ...application.ports import AssetRepositoryPort, EditServicePort
from ...cache.index_store import get_global_repository
from ...errors import LibraryUnavailableError
from ...utils.pathutils import ensure_work_dir
from .thumbnail_cache_service import ThumbnailCacheService


class LibraryAssetRuntime:
    """Own library-bound asset services so GUI code only rebinds roots.

    The runtime outlives any single library: it is constructed while the app
    boots, before the user has opened one. Asset access therefore has no
    fallback root — an unbound runtime raises :class:`LibraryUnavailableError`
    instead of silently opening a database and thumbnail cache under
    ``Path.home()`` (which left ``~/.iPhoto`` behind on every launch).
    """

    def __init__(self, library_root: Path | None = None) -> None:
        self._assets: AssetRepositoryPort | None = None
        self._thumbnail_service = ThumbnailCacheService(self._cache_root(library_root))
        self.bind_library_root(library_root)

    @property
    def assets(self) -> AssetRepositoryPort:
        repository = self._assets
        if repository is None:
            raise LibraryUnavailableError(
                "No library is bound: open a library before accessing assets."
            )
        return repository

    @property
    def repository(self) -> AssetRepositoryPort:
        return self.assets

    @property
    def thumbnail_service(self) -> ThumbnailCacheService:
        return self._thumbnail_service

    @property
    def is_bound(self) -> bool:
        """Whether a library root is currently bound to this runtime."""

        return self._assets is not None

    def bind_edit_service(self, edit_service: EditServicePort | None) -> None:
        """Bind the current library session edit surface into thumbnail rendering."""

        setter = getattr(self._thumbnail_service, "set_edit_service", None)
        if callable(setter):
            setter(edit_service)

    def bind_library_root(self, library_root: Path | None) -> None:
        """Rebuild the asset repository and cache path for *library_root*.

        ``library_root=None`` unbinds the runtime: that is the state the boot
        instance starts in and the state a closed library returns to.
        """

        if library_root is None:
            self._assets = None
            self._thumbnail_service.set_disk_cache_path(None)
            return
        self._assets = get_global_repository(Path(library_root))
        self._thumbnail_service.set_disk_cache_path(self._cache_root(library_root))

    def shutdown(self) -> None:
        self._thumbnail_service.shutdown()
        close = getattr(self._assets, "close", None)
        if callable(close):
            close()

    def _cache_root(self, library_root: Path | None) -> Path | None:
        if library_root is None:
            return None
        return ensure_work_dir(library_root) / "cache" / "thumbs"
