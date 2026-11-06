"""Web Mercator projection has one implementation.

Four map backends carried their own copy of the lon/lat projection, plus their own
``MERCATOR_LAT_BOUND``. The clamping is what keeps markers consistent between
backends, so a drifting constant moves pins by tens of metres on one backend only.
"""

from __future__ import annotations

import math

import pytest

from maps.map_widget.geometry import (
    MERCATOR_LAT_BOUND,
    lonlat_to_normalized,
    normalized_to_lonlat,
)


def _reference(lon: float, lat: float) -> tuple[float, float]:
    """The formula every backend used before consolidation."""

    lat = max(min(float(lat), MERCATOR_LAT_BOUND), -MERCATOR_LAT_BOUND)
    x = (float(lon) + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    return x, y


@pytest.mark.parametrize(
    "lon, lat",
    [(0.0, 0.0), (114.3, 30.5), (-180.0, -85.0), (180.0, 85.0), (44.58, 44.58)],
)
def test_projection_matches_the_previous_formula(lon: float, lat: float) -> None:
    expected = _reference(lon, lat)
    actual = lonlat_to_normalized(lon, lat)

    assert actual is not None
    assert actual[0] == pytest.approx(expected[0], abs=1e-12)
    assert actual[1] == pytest.approx(expected[1], abs=1e-12)


def test_latitude_is_clamped_to_the_mercator_limit() -> None:
    beyond = lonlat_to_normalized(0.0, 89.9)
    at_limit = lonlat_to_normalized(0.0, MERCATOR_LAT_BOUND)

    assert beyond is not None and at_limit is not None
    assert beyond[1] == pytest.approx(at_limit[1], abs=1e-12)


def test_roundtrip_returns_the_original_coordinates() -> None:
    projected = lonlat_to_normalized(114.3, 30.5)

    assert projected is not None
    lon, lat = normalized_to_lonlat(*projected)
    assert lon == pytest.approx(114.3, abs=1e-9)
    assert lat == pytest.approx(30.5, abs=1e-9)


def test_invalid_input_returns_none() -> None:
    assert lonlat_to_normalized("not-a-number", 0.0) is None
