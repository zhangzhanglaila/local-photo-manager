"""Lightweight search module for photo management.

This module provides search capabilities using existing metadata:
- Date/time search
- Location search
- Camera search
- Keyword search
- People search (using existing face recognition)

No large model downloads required.
"""

from .models.search_result import SearchResult

__all__ = ["SearchResult"]
