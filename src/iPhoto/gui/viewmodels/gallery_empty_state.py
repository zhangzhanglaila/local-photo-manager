"""Single source of truth for the gallery's empty-state placeholder.

``GalleryGridView`` used to juggle four independent flags (``_scan_completed``,
``_is_scanning``, ``_query_loading`` and ``_empty_mode``) and re-derived the
visible placeholder in several places, with the same "hide every overlay" block
copied four times.  This module folds the progress flags into one small state
machine that answers a single question: *which* placeholder should the view
paint for the current model?

The view only renders the returned enum; it never inspects the individual flags.
"""

from __future__ import annotations

from enum import Enum

from ...i18n import tr


class GalleryEmptyState(Enum):
    """The placeholder a grid with no rows should paint."""

    #: The first scan has not finished yet and the window-level startup overlay
    #: already reports progress; the grid must stay blank.
    INITIALIZING = "initializing"
    #: An index scan is still writing rows.
    SCANNING = "scanning"
    #: A query result is pending.
    LOADING = "loading"
    #: The result is genuinely empty; ``message()`` supplies the wording.
    EMPTY = "empty"
    #: Rows exist, so no placeholder may cover them.
    READY = "ready"


class GalleryEmptyStateViewModel:
    """Track scan/query progress and derive one :class:`GalleryEmptyState`."""

    __slots__ = ("_mode", "_query_loading", "_scan_completed", "_scanning")

    def __init__(self) -> None:
        self._scan_completed = False
        self._scanning = False
        self._query_loading = False
        self._mode: str | None = None

    # ------------------------------------------------------------------
    # Filter context
    # ------------------------------------------------------------------
    @property
    def mode(self) -> str | None:
        """Filter context that selects the empty message ("videos", ...)."""

        return self._mode

    def set_mode(self, mode: str | None) -> None:
        self._mode = mode

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------
    def scan_started(self) -> None:
        """A scan began (first run or a later re-scan of a new root/album)."""

        self._scanning = True

    def scan_finished(self) -> bool:
        """Record that a scan ended; return ``False`` when nothing changed."""

        if self._scan_completed and not self._scanning:
            return False
        self._scan_completed = True
        self._scanning = False
        self._query_loading = False
        return True

    def query_started(self) -> None:
        self._query_loading = True

    def query_finished(self) -> None:
        self._query_loading = False

    def rows_arrived(self) -> None:
        """Rows exist: nothing is loading and a filter's message no longer applies."""

        self._query_loading = False
        self._mode = None

    def dismiss_loading(self) -> None:
        """External code confirmed the content is ready; drop every overlay flag."""

        self._scanning = False
        self._query_loading = False
        self._scan_completed = True
        self._mode = None

    # ------------------------------------------------------------------
    # Derivation
    # ------------------------------------------------------------------
    def state(self, has_rows: bool) -> GalleryEmptyState:
        """Return the placeholder for a viewport with or without rows.

        Priority for an empty viewport: scanning > loading > initializing >
        empty.  Rows always win: a stale overlay must never cover thumbnails.
        """

        if has_rows:
            return GalleryEmptyState.READY
        if self._scanning:
            return GalleryEmptyState.SCANNING
        if self._query_loading:
            return GalleryEmptyState.LOADING
        if not self._scan_completed:
            return GalleryEmptyState.INITIALIZING
        return GalleryEmptyState.EMPTY

    def message(self) -> str:
        """Wording for :attr:`GalleryEmptyState.EMPTY`, aware of the filter."""

        mode = self._mode
        if mode is None:
            # No filter context: the generic "nothing here, try a rescan" hint.
            return tr("empty.no_media")
        if mode == "favorites":
            return tr("empty.favorites")
        if mode == "videos":
            return tr("empty.videos")
        if mode == "live":
            return tr("empty.live")
        if mode == "no_library":
            return tr("empty.no_library")
        return tr("empty.no_photos")
