"""Runtime-owned asset repository and thumbnail service rebinding."""

from __future__ import annotations

from pathlib import Path

from ...application.library_context import LibraryContext, LibraryContextFactory
from ...application.ports import AssetRepositoryPort, EditServicePort
from ...errors import LibraryUnavailableError
from .thumbnail_cache_service import ThumbnailCacheService


class LibraryAssetRuntime:
    """Own library-bound asset services so GUI code only rebinds roots.

    The runtime outlives any single library: it is constructed while the app
    boots, before the user has opened one. Asset access therefore has no
    fallback root — an unbound runtime raises :class:`LibraryUnavailableError`
    instead of silently opening a database and thumbnail cache under
    ``Path.home()`` (which left ``~/.iPhoto`` behind on every launch).

    The repository is obtained from an injected :class:`LibraryContextFactory`,
    so this module never reaches for the process-wide index store itself.
    """

    def __init__(
        self,
        library_root: Path | None = None,
        *,
        contexts: LibraryContextFactory,
    ) -> None:
        self._contexts = contexts
        self._context: LibraryContext | None = None
        self._thumbnail_service = ThumbnailCacheService(None)
        self.bind_library_root(library_root)

    @property
    def context(self) -> LibraryContext:
        """Return the bound library context, or raise when no library is open."""

        context = self._context
        if context is None:
            raise LibraryUnavailableError(
                "No library is bound: open a library before accessing assets."
            )
        return context

    @property
    def assets(self) -> AssetRepositoryPort:
        return self.context.repository

    @property
    def repository(self) -> AssetRepositoryPort:
        return self.assets

    @property
    def thumbnail_service(self) -> ThumbnailCacheService:
        return self._thumbnail_service

    @property
    def is_bound(self) -> bool:
        """Whether a library root is currently bound to this runtime."""

        return self._context is not None

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
            self._context = None
            self._thumbnail_service.set_disk_cache_path(None)
            return
        context = self._contexts.open(Path(library_root))
        self._context = context
        self._thumbnail_service.set_disk_cache_path(context.thumbnail_cache_dir)

    def shutdown(self) -> None:
        self._thumbnail_service.shutdown()
        repository = self._context.repository if self._context is not None else None
        close = getattr(repository, "close", None)
        if callable(close):
            close()


__all__ = ["LibraryAssetRuntime"]
