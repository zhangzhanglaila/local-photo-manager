"""Applying a finished load must not back up behind the drag.

Measured on the real library: the loader finished in 70–650 ms, yet the result reached
the main thread 2–4 s later. The loader was fine; the main thread was saturated by
repaints and model resets, so the queued result waited. Throttling how often a finished
load is applied keeps at most one reset per interval during a drag, and the newest
result is applied immediately on release.

These tests cover the throttle bookkeeping itself; the apply body is exercised by the
gallery tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _Service:
    def count_query_assets(self, query) -> int:  # pragma: no cover - not used
        return 0


def _store(tmp_path: Path) -> GalleryCollectionStore:
    root = tmp_path / "Library"
    root.mkdir()
    store = GalleryCollectionStore(_Service(), root)
    store._total_count = 10
    return store


def test_first_result_applies_and_the_next_is_deferred(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_apply_throttle(10.0)

    store._on_load_finished(1, {0: object()}, 10)
    assert store._deferred_result is None, "the first result is applied right away"

    store._on_load_finished(2, {1: object()}, 10)
    assert store._deferred_result is not None
    assert store._deferred_result[0] == 2, "the newest result is the one kept"


def test_release_clears_the_deferred_slot_after_applying(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_apply_throttle(10.0)
    store._on_load_finished(1, {0: object()}, 10)
    store._on_load_finished(2, {1: object()}, 10)
    assert store._deferred_result is not None

    store.set_apply_throttle(0.0)
    store.flush_deferred_load()

    assert store._deferred_result is None


def test_no_throttle_never_defers(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store._on_load_finished(1, {0: object()}, 10)
    store._on_load_finished(2, {1: object()}, 10)

    assert store._deferred_result is None
