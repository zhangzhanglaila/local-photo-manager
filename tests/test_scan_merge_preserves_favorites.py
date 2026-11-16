"""A rescan must not wipe library-managed state such as favourites.

The scanner emits these fields with neutral defaults (``is_favorite: 0``). The merge
used to keep the scanned value whenever the key was present, so every start cleared the
user's favourites.
"""

from __future__ import annotations

from iPhoto.cache.index_store.scan_merge import merge_scan_row


def test_favourite_survives_a_rescan_that_reports_the_default() -> None:
    scanned = {"rel": "album/a.jpg", "is_favorite": 0, "dt": "2024:01:01"}
    existing = {"rel": "album/a.jpg", "is_favorite": 1}

    merged = merge_scan_row(scanned, existing)

    assert merged["is_favorite"] == 1, "the persisted favourite wins over the default"


def test_unfavourited_state_is_not_restored_by_a_rescan() -> None:
    scanned = {"rel": "album/b.jpg", "is_favorite": 1}
    existing = {"rel": "album/b.jpg", "is_favorite": 0}

    merged = merge_scan_row(scanned, existing)

    assert merged["is_favorite"] == 0, "a deliberate 0 is persisted state too"


def test_missing_persisted_value_falls_back_to_the_scanned_row() -> None:
    scanned = {"rel": "album/c.jpg", "live_role": "video"}
    existing = {"rel": "album/c.jpg", "live_role": None}

    merged = merge_scan_row(scanned, existing)

    assert merged["live_role"] == "video", "an empty persisted value is not authoritative"
