"""Trail/timeline data models for map visualization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class TrailPoint:
    """A single point on the photo trail."""

    asset_id: str
    asset_rel: str
    latitude: float
    longitude: float
    timestamp: datetime
    thumbnail_path: Optional[str] = None
    location_name: Optional[str] = None


@dataclass
class TrailSegment:
    """A continuous trail segment (same day/week/month)."""

    points: list[TrailPoint] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    color: tuple[int, int, int] = (66, 133, 244)  # RGB, default blue

    def __post_init__(self) -> None:
        if self.points and self.start_time is None:
            self.start_time = self.points[0].timestamp
        if self.points and self.end_time is None:
            self.end_time = self.points[-1].timestamp


@dataclass
class TrailData:
    """Complete trail data for map rendering."""

    segments: list[TrailSegment] = field(default_factory=list)
    total_photos: int = 0
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    @property
    def is_empty(self) -> bool:
        return self.total_photos == 0
