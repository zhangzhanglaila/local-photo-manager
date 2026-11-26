"""Live state of the search index, shared by the encoder, the indexer and the UI.

The settings page needs to answer three questions at a glance: which model is
active, where it runs (GPU or CPU), and whether vectors are still being built.
Keeping that in one small observable object avoids reaching into the coordinator
from the settings window.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class SearchIndexStatus(QObject):
    """Observable snapshot of the semantic-search index."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._model_key: str = ""
        self._device: str = ""  # "" = not detected yet
        self._dtype: str = ""
        self._running: bool = False
        self._done: int = 0
        self._total: int = 0

    # -- accessors -----------------------------------------------------
    @property
    def model_key(self) -> str:
        return self._model_key

    @property
    def device(self) -> str:
        return self._device

    @property
    def dtype(self) -> str:
        return self._dtype

    @property
    def running(self) -> bool:
        return self._running

    @property
    def done(self) -> int:
        return self._done

    @property
    def total(self) -> int:
        return self._total

    # -- updates -------------------------------------------------------
    def set_model(self, key: str) -> None:
        if key == self._model_key:
            return
        self._model_key = key
        self.changed.emit()

    def set_device(self, device: str, dtype: str = "") -> None:
        if device == self._device and dtype == self._dtype:
            return
        self._device = device
        self._dtype = dtype
        self.changed.emit()

    def set_indexing(self, running: bool, done: int = 0, total: int = 0) -> None:
        if (running, done, total) == (self._running, self._done, self._total):
            return
        self._running = running
        self._done = done
        self._total = total
        self.changed.emit()

    def set_coverage(self, done: int, total: int) -> None:
        """Record how many photos of *total* already have vectors."""

        self.set_indexing(False, done, total)

    def coverage_text(self) -> str:
        if self._total <= 0:
            return ""
        return f"{self._done}/{self._total}"


_status: SearchIndexStatus | None = None


def search_status() -> SearchIndexStatus:
    """Return the process-wide status object (created on first use)."""

    global _status
    if _status is None:
        _status = SearchIndexStatus()
    return _status


__all__ = ["SearchIndexStatus", "search_status"]
