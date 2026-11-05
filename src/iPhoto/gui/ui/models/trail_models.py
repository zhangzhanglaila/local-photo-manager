"""Trail/timeline data models for map visualization.

The dataclasses live in :mod:`iPhoto.domain.models.trail` because an application
service builds them; importing them from the GUI layer violated the layer
boundaries. They are re-exported here so existing GUI imports keep working.
"""

from __future__ import annotations

from ....domain.models.trail import TrailData, TrailPoint, TrailSegment

__all__ = ["TrailData", "TrailPoint", "TrailSegment"]