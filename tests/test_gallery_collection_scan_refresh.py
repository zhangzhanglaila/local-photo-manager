"""Regression tests for scan-driven gallery window refreshes.

These live outside ``tests/gui`` on purpose: the store is plain Python with a
lightweight signal implementation, it does not need Qt widgets, and the GUI
directory is excluded from the default test run. A guard that never executes
does not guard anything.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _StubQueryService:
    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root

    def count_query_assets(self, query: AssetQuery) -> int:
        del query
        return 0

    def read_query_asset_rows(self, root: Path, query: AssetQuery):
        del root, query
        return []


def _store(root: Path) -> GalleryCollectionStore:
    store = GalleryCollectionStore(_StubQueryService(root), library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    return store


def test_scan_refreshes_are_coalesced_and_consumed(tmp_path: Path, monkeypatch) -> None:
    """Scan-driven reloads must be bounded and must not repeat once applied.

    Regression: the pending-scan flag was never cleared after a reload ran, so
    every ``prioritize_rows`` call — emitted by the grid on each repaint —
    reloaded the window again. A finished scan therefore kept the gallery
    resetting continuously, which is what made browsing lag.
    """

    import iPhoto.gui.viewmodels.gallery_collection_store as store_module

    root = tmp_path / "Library"
    root.mkdir()
    store = _store(root)

    reloads: list[tuple[int, int]] = []
    monkeypatch.setattr(
        store,
        "_reload_window_for_visible_range",
        lambda first, last, *, emit_signals: reloads.append((first, last)),
    )
    monkeypatch.setattr(store, "_window_needs_reload", lambda first, last: False)

    # Seed the state a loaded gallery would have, without going through the
    # asynchronous window loader.
    store._active_root = root
    store._current_query = AssetQuery()
    store._total_count = 240
    store._visible_range = (0, 49)
    store._window_range = (0, 49)

    clock = {"now": 100.0}
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    chunk = [{"rel": "asset_0.jpg", "id": "1000", "dt": datetime(2024, 1, 1, 12).isoformat()}]

    # One scan chunk followed by the repaint-driven prioritisation storm the
    # grid produces: the chunk may only cost a single reload.
    store.handle_scan_chunk(root, chunk)
    for _ in range(10):
        store.prioritize_rows(0, 49)
    assert len(reloads) == 1, f"scan refresh should coalesce to one reload, got {len(reloads)}"
    assert store._pending_scan_refresh is False, "the applied refresh must be consumed"

    # With the pending state consumed, idle repaints must not reload again.
    reloads.clear()
    for _ in range(10):
        clock["now"] += 1.0
        store.prioritize_rows(0, 49)
    assert reloads == [], "idle repaints must not reload once the scan refresh was applied"


def test_scan_chunks_do_not_reload_faster_than_the_coalescing_interval(
    tmp_path: Path, monkeypatch
) -> None:
    """A burst of chunks may not turn into a burst of model resets."""

    import iPhoto.gui.viewmodels.gallery_collection_store as store_module

    root = tmp_path / "Library"
    root.mkdir()
    store = _store(root)

    reloads: list[tuple[int, int]] = []
    monkeypatch.setattr(
        store,
        "_reload_window_for_visible_range",
        lambda first, last, *, emit_signals: reloads.append((first, last)),
    )
    monkeypatch.setattr(store, "_window_needs_reload", lambda first, last: False)

    store._active_root = root
    store._current_query = AssetQuery()
    store._total_count = 240
    store._visible_range = (0, 49)
    store._window_range = (0, 49)

    clock = {"now": 100.0}
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    chunk = [{"rel": "asset_0.jpg", "id": "1000", "dt": datetime(2024, 1, 1, 12).isoformat()}]
    for _ in range(20):
        clock["now"] += 0.01  # 20 chunks within 200 ms
        store.handle_scan_chunk(root, chunk)

    # 200 ms of chunks may span at most two coalescing intervals, versus the 20
    # reloads this used to cause (one per chunk).
    assert 1 <= len(reloads) <= 2, (
        f"20 chunks inside 200 ms should coalesce to at most 2 reloads, got {len(reloads)}"
    )


def test_window_edges_respect_collection_bounds(tmp_path: Path) -> None:
    """Boundary windows must not report a permanent reload need.

    Regression: the look-behind/look-ahead margin stayed inside the cached
    window, so ``first < safe_first`` was true for every visible range starting
    at row 0 — the default scroll position. Each reload rebuilt the same window,
    reset the model, and asked the question again.
    """

    store = _store(tmp_path)
    store._total_count = 200
    store._window_range = (0, 149)
    store._row_cache = {row: object() for row in range(150)}  # type: ignore[assignment]

    # Top of the collection: nothing can be loaded above row 0.
    assert store._window_needs_reload(0, 49) is False

    # Inside the collection the hysteresis still applies: a window whose left
    # edge sits at the visible edge must be reloaded to keep look-behind.
    store._total_count = 600
    store._window_range = (100, 249)
    store._row_cache = {row: object() for row in range(100, 250)}  # type: ignore[assignment]
    assert store._window_needs_reload(100, 149) is True
    assert store._window_needs_reload(150, 199) is False

    # Bottom of the collection: nothing can be loaded below the last row.
    store._window_range = (450, 599)
    store._row_cache = {row: object() for row in range(450, 600)}  # type: ignore[assignment]
    assert store._window_needs_reload(550, 599) is False
