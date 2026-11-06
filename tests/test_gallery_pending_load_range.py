"""A deferred load must resume where the user was, not at row 0.

When the gallery is asked to show a range before the query service is bound, the
request is parked in ``_pending_load_range``. The binding handler then unpacked
that range into ``first, last`` — and dropped both, calling the parameterless
``_load_initial_window()``, which always loads rows ``0..N``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore


class _FakeService:
    """Placeholder query surface; the store only checks that it is not None."""


def _store(tmp_path: Path) -> GalleryCollectionStore:
    root = tmp_path / "Library"
    root.mkdir()
    return GalleryCollectionStore(None, root)


def test_deferred_load_keeps_the_requested_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    store.set_active_root(tmp_path / "Library")

    # No service yet: the range is parked.
    store._reload_window_for_visible_range(500, 540, emit_signals=False)
    assert store._pending_load_range == (500, 540)

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        store,
        "_reload_window_for_visible_range",
        lambda first, last, *, emit_signals: calls.append((first, last)),
    )

    store.set_asset_query_service(_FakeService())

    assert calls == [(500, 540)], "the deferred range must be replayed, not reset to row 0"
    assert store._pending_load_range is None


def test_initial_window_without_a_range_still_starts_at_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        store,
        "_reload_window_for_visible_range",
        lambda first, last, *, emit_signals: calls.append((first, last)),
    )

    store._load_initial_window()

    assert calls == [(0, store.INITIAL_VISIBLE_ROWS - 1)]
    assert store._visible_range == (0, store.INITIAL_VISIBLE_ROWS - 1)


def test_explicit_range_is_normalised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        store,
        "_reload_window_for_visible_range",
        lambda first, last, *, emit_signals: calls.append((first, last)),
    )

    store._load_initial_window((-5, -1))

    assert calls == [(0, 0)], "negative input is clamped instead of producing an empty window"
