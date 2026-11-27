"""Unit tests for the gallery empty-state state machine.

``GalleryGridView`` used to derive its placeholder from four independent flags
in several places.  The derivation now lives in
:mod:`iPhoto.gui.viewmodels.gallery_empty_state`; these tests pin the priority
rules the view relies on.
"""

from __future__ import annotations

import pytest

from iPhoto.gui.viewmodels.gallery_empty_state import (
    GalleryEmptyState,
    GalleryEmptyStateViewModel,
)
from iPhoto.i18n import tr


@pytest.fixture()
def state() -> GalleryEmptyStateViewModel:
    return GalleryEmptyStateViewModel()


def test_initializing_until_the_first_scan_finishes(state: GalleryEmptyStateViewModel) -> None:
    assert state.state(has_rows=False) is GalleryEmptyState.INITIALIZING
    assert state.state(has_rows=True) is GalleryEmptyState.READY


def test_scanning_outranks_a_pending_query(state: GalleryEmptyStateViewModel) -> None:
    state.scan_started()
    state.query_started()

    assert state.state(has_rows=False) is GalleryEmptyState.SCANNING


def test_a_pending_query_is_loading_once_the_scan_is_done(state: GalleryEmptyStateViewModel) -> None:
    state.scan_finished()
    state.query_started()

    assert state.state(has_rows=False) is GalleryEmptyState.LOADING


def test_scan_finished_reports_a_change_only_once(state: GalleryEmptyStateViewModel) -> None:
    assert state.scan_finished() is True
    assert state.scan_finished() is False
    assert state.state(has_rows=False) is GalleryEmptyState.EMPTY


def test_a_rescan_after_the_first_scan_shows_the_scanning_hint(state: GalleryEmptyStateViewModel) -> None:
    state.scan_finished()
    state.query_started()
    state.scan_started()

    assert state.state(has_rows=False) is GalleryEmptyState.SCANNING


def test_rows_arriving_clear_the_filter_message(state: GalleryEmptyStateViewModel) -> None:
    state.scan_finished()
    state.set_mode("videos")
    state.query_started()

    state.rows_arrived()

    assert state.mode is None
    assert state.state(has_rows=True) is GalleryEmptyState.READY
    assert state.state(has_rows=False) is GalleryEmptyState.EMPTY


def test_rows_always_win_over_a_stale_overlay(state: GalleryEmptyStateViewModel) -> None:
    state.scan_started()
    state.query_started()

    assert state.state(has_rows=True) is GalleryEmptyState.READY


def test_dismiss_loading_forces_the_settled_state(state: GalleryEmptyStateViewModel) -> None:
    state.scan_started()
    state.query_started()
    state.set_mode("favorites")

    state.dismiss_loading()

    assert state.mode is None
    assert state.state(has_rows=False) is GalleryEmptyState.EMPTY


@pytest.mark.parametrize(
    ("mode", "key"),
    [
        ("favorites", "empty.favorites"),
        ("videos", "empty.videos"),
        ("live", "empty.live"),
        ("no_library", "empty.no_library"),
        (None, "empty.no_media"),
        ("unknown", "empty.no_photos"),
    ],
)
def test_message_follows_the_filter_context(
    state: GalleryEmptyStateViewModel, mode: str | None, key: str
) -> None:
    state.set_mode(mode)

    assert state.message() == tr(key)


def test_setting_the_mode_does_not_change_the_derived_state(state: GalleryEmptyStateViewModel) -> None:
    before = state.state(has_rows=False)
    state.set_mode("videos")

    assert state.state(has_rows=False) is before
