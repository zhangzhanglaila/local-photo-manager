"""Trail building service for photo timeline visualization."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

from ...domain.models.trail import TrailData, TrailPoint, TrailSegment

if TYPE_CHECKING:
    from ...library.geo_aggregator import GeotaggedAsset

_LOGGER = logging.getLogger(__name__)

# Color palette for trail segments (RGB tuples)
_SEGMENT_COLORS = [
    (66, 133, 244),   # Blue
    (234, 67, 53),    # Red
    (251, 188, 4),    # Yellow
    (52, 168, 83),    # Green
    (171, 71, 188),   # Purple
    (255, 112, 67),   # Orange
    (0, 172, 193),    # Cyan
    (124, 179, 66),   # Light Green
    (233, 30, 99),    # Pink
    (156, 39, 176),   # Deep Purple
]


class TrailService:
    """Builds trail data from geotagged photos for map visualization."""

    def build_trail(
        self,
        geotagged_assets: List["GeotaggedAsset"],
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        granularity: str = "day",
    ) -> TrailData:
        """Build trail data from geotagged photos.

        Parameters
        ----------
        geotagged_assets : list[GeotaggedAsset]
            All geotagged photos in the library.
        date_from : datetime, optional
            Start date filter (None = earliest).
        date_to : datetime, optional
            End date filter (None = latest).
        granularity : str
            Grouping granularity: "day", "week", or "month".

        Returns
        -------
        TrailData
            Trail segments ready for rendering.
        """
        if not geotagged_assets:
            return TrailData()

        # Convert epoch timestamps to datetime and filter out assets
        # that have no valid timestamp.
        # Prefer ``still_image_time`` (Live Photo hero-frame time), falling
        # back to the generic ``timestamp`` field (file mtime / EXIF date).
        def _ts(a):
            if a.still_image_time is not None:
                return datetime.fromtimestamp(a.still_image_time)
            if a.timestamp is not None:
                return datetime.fromtimestamp(a.timestamp)
            return None

        filtered = [(a, _ts(a)) for a in geotagged_assets]
        filtered = [(a, ts) for a, ts in filtered if ts is not None]

        if date_from is not None:
            filtered = [(a, ts) for a, ts in filtered if ts >= date_from]
        if date_to is not None:
            filtered = [(a, ts) for a, ts in filtered if ts <= date_to]

        if not filtered:
            return TrailData()

        # Sort by timestamp
        filtered.sort(key=lambda pair: pair[1])

        # Convert to TrailPoint
        points = [
            TrailPoint(
                asset_id=a.asset_id,
                asset_rel=a.album_relative,
                latitude=a.latitude,
                longitude=a.longitude,
                timestamp=ts,
                location_name=a.location_name,
            )
            for a, ts in filtered
        ]

        # Group by granularity, splitting on gaps > 24h
        segments = self._group_points(points, granularity)

        return TrailData(
            segments=segments,
            total_photos=len(points),
            start_date=points[0].timestamp if points else None,
            end_date=points[-1].timestamp if points else None,
        )

    def _group_points(
        self, points: List[TrailPoint], granularity: str
    ) -> List[TrailSegment]:
        """Group points into segments by time granularity.

        Points are grouped by day/week/month. If there's a gap > 24 hours
        within a group, it's split into separate segments.
        """
        if not points:
            return []

        # Group by time key
        groups: dict[str, list[TrailPoint]] = defaultdict(list)
        for p in points:
            key = self._time_key(p.timestamp, granularity)
            groups[key].append(p)

        # Build segments, splitting on gaps
        segments: List[TrailSegment] = []
        color_idx = 0

        for key in sorted(groups.keys()):
            group = groups[key]
            # Split on gaps > 24h
            sub_groups: list[list[TrailPoint]] = [[group[0]]]
            for prev, curr in zip(group, group[1:]):
                gap = (curr.timestamp - prev.timestamp).total_seconds()
                if gap > 86400:  # 24 hours
                    sub_groups.append([curr])
                else:
                    sub_groups[-1].append(curr)

            for sg in sub_groups:
                if not sg:
                    continue
                color = _SEGMENT_COLORS[color_idx % len(_SEGMENT_COLORS)]
                segments.append(TrailSegment(
                    points=sg,
                    start_time=sg[0].timestamp,
                    end_time=sg[-1].timestamp,
                    color=color,
                ))
                color_idx += 1

        return segments

    @staticmethod
    def _time_key(dt: datetime, granularity: str) -> str:
        """Generate a grouping key for the given datetime."""
        if granularity == "week":
            # ISO week: year-Www
            iso = dt.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        elif granularity == "month":
            return dt.strftime("%Y-%m")
        else:  # day
            return dt.strftime("%Y-%m-%d")
