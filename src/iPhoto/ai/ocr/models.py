"""OCR domain data classes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OCRRegion:
    """A detected text region in an image."""

    text: str
    """Extracted text content."""

    confidence: float
    """Detection confidence (0.0 to 1.0)."""

    box_x: float
    """Bounding box left coordinate."""

    box_y: float
    """Bounding box top coordinate."""

    box_w: float
    """Bounding box width."""

    box_h: float
    """Bounding box height."""


@dataclass(frozen=True)
class OCRAssetText:
    """All recognized text for one photo, for the settings browser."""

    asset_id: str
    asset_rel: str
    text: str
    confidence: float


@dataclass(frozen=True)
class OCRSearchResult:
    """Result from an OCR text search."""

    asset_id: str
    """Unique identifier for the asset."""

    asset_rel: str
    """Library-relative path to the asset."""

    text: str
    """Matched text content."""

    confidence: float
    """OCR confidence score."""

    rank: float
    """FTS5 ranking score."""

    snippet: str = ""
    """Highlighted text snippet."""
