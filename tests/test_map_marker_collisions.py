"""Map markers must never overlap: closed badges hide each other's counts.

The clustering threshold only keeps cluster *centres* apart. With a 72 px marker,
two centres 48 px apart produced two badges drawn on top of each other, and the
visible numbers no longer matched what the clusters contained.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets are required")

from PySide6.QtCore import QPointF

from iPhoto.gui.ui.widgets.marker_controller import (
    _MarkerCluster,
    _merge_overlapping_clusters,
)


class _Asset:
    """Minimal geotagged asset stand-in."""

    def __init__(self, latitude: float, longitude: float, rel: str) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.library_relative = rel


def _cluster(x: float, y: float, count: int = 1) -> _MarkerCluster:
    assets = [_Asset(30.0 + i * 0.001, 114.0 + i * 0.001, f"a{i}.jpg") for i in range(count)]
    cluster = _MarkerCluster(
        representative=assets[0],
        assets=assets,
        screen_pos=QPointF(x, y),
    )
    cluster.screen_x_sum = x * count
    cluster.screen_y_sum = y * count
    return cluster


def test_overlapping_markers_are_merged() -> None:
    # 40 px apart with a 72 px footprint + gap: they would overlap on screen.
    clusters = [_cluster(100.0, 100.0, 3), _cluster(140.0, 100.0, 2)]

    merged = _merge_overlapping_clusters(clusters, 80.0)

    assert len(merged) == 1
    assert len(merged[0].assets) == 5, "every asset is still counted exactly once"


def test_distant_markers_stay_separate() -> None:
    clusters = [_cluster(100.0, 100.0), _cluster(300.0, 300.0)]

    merged = _merge_overlapping_clusters(clusters, 80.0)

    assert len(merged) == 2


def test_no_badge_overlap_survives_a_chain() -> None:
    """After merging, every pair of markers must be at least a footprint apart.

    A naive single pass could leave A and C overlapping once B was folded into A,
    so the invariant (not the cluster count) is what matters here.
    """

    clusters = [_cluster(100.0, 100.0), _cluster(160.0, 100.0), _cluster(220.0, 100.0)]

    merged = _merge_overlapping_clusters(clusters, 80.0)

    assert sum(len(cluster.assets) for cluster in merged) == 3
    for i, left in enumerate(merged):
        for right in merged[i + 1:]:
            distance = (
                (left.screen_pos.x() - right.screen_pos.x()) ** 2
                + (left.screen_pos.y() - right.screen_pos.y()) ** 2
            ) ** 0.5
            assert distance >= 80.0, "two badges would still overlap"


def test_merged_cluster_keeps_a_weighted_centre() -> None:
    clusters = [_cluster(100.0, 100.0, 1), _cluster(150.0, 100.0, 3)]

    merged = _merge_overlapping_clusters(clusters, 80.0)

    assert len(merged) == 1
    # One marker at x=100 and three at x=150.
    assert merged[0].screen_pos.x() == pytest.approx((100.0 + 150.0 * 3) / 4.0, abs=0.5)
