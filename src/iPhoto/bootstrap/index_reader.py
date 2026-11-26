"""Read-only probes over the library asset index for the GUI layer.

The settings window, the location search and the "does this library need a scan?"
dialog all used to import ``iPhoto.cache.index_store`` directly (or open sqlite by
hand). They now ask these two functions, so the concrete store stays in the
composition/bootstrap layer and the GUI keeps talking to a narrow surface.
"""

from __future__ import annotations

from pathlib import Path

MISSING = "missing"
EMPTY = "empty"
READY = "ready"


def library_index_state(library_root: Path) -> str:
    """Return ``"missing"``, ``"empty"`` or ``"ready"`` for *library_root*.

    ``empty`` includes an unreadable database: the caller's decision ("scan this
    library") is the same, and it must not crash on a corrupt file.
    """

    from ..cache.index_store import IndexStore, index_db_path

    if not index_db_path(library_root).exists():
        return MISSING
    store = IndexStore(library_root)
    try:
        return READY if store.count() > 0 else EMPTY
    except Exception:  # noqa: BLE001 - corrupt index means "needs a scan"
        return EMPTY
    finally:
        store.close()


def read_all_asset_rows(library_root: Path) -> list[dict]:
    """Return every asset row as a dict (empty when the library was never scanned)."""

    from ..cache.index_store import IndexStore, index_db_path

    if not index_db_path(library_root).exists():
        return []
    store = IndexStore(library_root)
    try:
        return list(store.read_all())
    finally:
        store.close()


__all__ = ["EMPTY", "MISSING", "READY", "library_index_state", "read_all_asset_rows"]
