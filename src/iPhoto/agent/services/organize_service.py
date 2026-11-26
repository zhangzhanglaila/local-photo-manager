"""Smart organization service for photos (metadata-based)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ...i18n import tr

_LOGGER = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    """A group of duplicate or near-duplicate photos."""

    asset_ids: List[str]
    """List of asset IDs in the group."""

    asset_rels: List[str]
    """List of relative paths."""

    similarity: float
    """Similarity score."""


@dataclass
class SmartAlbum:
    """A smart album suggestion."""

    name: str
    """Suggested album name."""

    description: str
    """Album description."""

    asset_ids: List[str]
    """List of asset IDs to include."""

    album_type: str
    """Type of album (event, location, time, theme)."""


class OrganizeService:
    """Smart organization service for photos.

    Uses metadata for organization without requiring large models.
    """

    def __init__(
        self,
        asset_repository: object,
        library_root: Path,
    ) -> None:
        """Initialize the organize service.

        Parameters
        ----------
        asset_repository : object
            Repository for accessing asset data.
        library_root : Path
            Root directory of the library.
        """
        self._asset_repository = asset_repository
        self._library_root = library_root

    def find_duplicates(self, threshold: float = 0.95) -> List[DuplicateGroup]:
        """Find duplicate photos based on file hash or metadata.

        Parameters
        ----------
        threshold : float
            Similarity threshold (not used for hash-based detection).

        Returns
        -------
        List[DuplicateGroup]
            Groups of duplicate photos.
        """
        all_assets = self._asset_repository.read_all()
        if not all_assets:
            return []

        # Group by file size + dimensions (simple duplicate detection)
        groups = defaultdict(list)
        for asset in all_assets:
            # Create a key from size and dimensions
            size = asset.get("bytes", 0)
            w = asset.get("w", 0)
            h = asset.get("h", 0)
            key = f"{size}_{w}_{h}"

            if size > 0 and w > 0 and h > 0:
                groups[key].append(asset)

        # Filter groups with multiple assets
        result_groups = []
        for key, assets in groups.items():
            if len(assets) >= 2:
                asset_ids = [a.get("id", "") for a in assets]
                asset_rels = [a.get("rel", "") for a in assets]
                result_groups.append(DuplicateGroup(
                    asset_ids=asset_ids,
                    asset_rels=asset_rels,
                    similarity=1.0,
                ))

        # Sort by group size (largest first)
        result_groups.sort(key=lambda g: len(g.asset_ids), reverse=True)

        return result_groups

    def create_smart_albums(self, group_by: str = "event") -> List[SmartAlbum]:
        """Create smart album suggestions.

        Parameters
        ----------
        group_by : str
            How to group photos: 'event', 'location', 'time'.

        Returns
        -------
        List[SmartAlbum]
            List of smart album suggestions.
        """
        all_assets = self._asset_repository.read_all()
        if not all_assets:
            return []

        if group_by == "time":
            return self._group_by_time(all_assets)
        elif group_by == "location":
            return self._group_by_location(all_assets)
        elif group_by == "event":
            return self._group_by_event(all_assets)

        return []

    def _group_by_time(self, assets: list) -> List[SmartAlbum]:
        """Group photos by time periods."""
        groups = defaultdict(list)
        for asset in assets:
            dt = asset.get("dt", "")
            if dt and len(dt) >= 7:
                year_month = dt[:7]  # YYYY-MM
                groups[year_month].append(asset.get("id", ""))

        albums = []
        for year_month, asset_ids in groups.items():
            if len(asset_ids) >= 5:
                albums.append(SmartAlbum(
                    name=tr("organize.smart_album.month_name", period=year_month),
                    description=tr(
                        "organize.smart_album.month_description", period=year_month
                    ),
                    asset_ids=asset_ids,
                    album_type="time",
                ))

        return albums

    def _group_by_location(self, assets: list) -> List[SmartAlbum]:
        """Group photos by location."""
        groups = defaultdict(list)
        for asset in assets:
            location = asset.get("location", "")
            if location:
                groups[location].append(asset.get("id", ""))

        albums = []
        for location, asset_ids in groups.items():
            if len(asset_ids) >= 3:
                albums.append(SmartAlbum(
                    name=tr("organize.smart_album.location_name", location=location),
                    description=tr(
                        "organize.smart_album.location_description", location=location
                    ),
                    asset_ids=asset_ids,
                    album_type="location",
                ))

        return albums

    def _group_by_event(self, assets: list) -> List[SmartAlbum]:
        """Group photos by events (clusters of photos taken close together in time)."""
        # Sort by timestamp
        sorted_assets = sorted(assets, key=lambda a: a.get("ts", 0))

        # Find clusters (photos taken within 1 hour of each other)
        clusters = []
        current_cluster = []
        last_ts = 0

        for asset in sorted_assets:
            ts = asset.get("ts", 0)
            if ts - last_ts > 3600 * 1000000:  # 1 hour in microseconds
                if current_cluster:
                    clusters.append(current_cluster)
                current_cluster = []
            current_cluster.append(asset)
            last_ts = ts

        if current_cluster:
            clusters.append(current_cluster)

        albums = []
        for i, cluster in enumerate(clusters):
            if len(cluster) >= 5:
                dates = [a.get("dt", "") for a in cluster if a.get("dt")]
                if dates:
                    start_date = min(dates)[:10]
                    end_date = max(dates)[:10]
                    if start_date != end_date:
                        name = tr(
                            "organize.smart_album.event_name_range",
                            index=i + 1,
                            start=start_date,
                            end=end_date,
                        )
                    else:
                        name = tr(
                            "organize.smart_album.event_name",
                            index=i + 1,
                            start=start_date,
                        )

                    albums.append(SmartAlbum(
                        name=name,
                        description=tr(
                            "organize.smart_album.event_description",
                            start=start_date,
                            end=end_date,
                        ),
                        asset_ids=[a.get("id", "") for a in cluster],
                        album_type="event",
                    ))

        return albums
