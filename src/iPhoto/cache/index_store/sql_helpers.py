"""Small SQL/JSON helpers shared by the index store's read and write halves.

These used to sit in ``repository.py``; the read mixin and the write mixin both
need them, and importing them from the repository would produce a cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

# "One still photo" in SQL. Nothing marks rows as deleted (deletion removes the
# row), so media type plus the live-photo role is the whole filter: videos are
# ``media_type=1`` and a live photo's motion clip is a second row with a
# non-null ``live_role``. Both coverage screens count this population, so the
# filter lives here instead of being copy-pasted into each caller.
PHOTO_ROW_FILTER = "media_type = 0 AND (live_role IS NULL OR live_role = 0)"

# SQLite supports at most SQLITE_MAX_VARIABLE_NUMBER bound parameters per query
# (compile-time default 999, raised to 32766 in SQLite >= 3.32).  Use a
# conservative chunk size so large scans never hit the limit regardless of
# the SQLite version in use.
_SQLITE_PARAM_CHUNK_SIZE = 900
_OMIT_METADATA_VALUE = object()

def _coerce_json_metadata_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _OMIT_METADATA_VALUE
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            sanitized_item = _coerce_json_metadata_value(item)
            if sanitized_item is _OMIT_METADATA_VALUE:
                continue
            sanitized[str(key)] = sanitized_item
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_items = []
        for item in value:
            sanitized_item = _coerce_json_metadata_value(item)
            if sanitized_item is _OMIT_METADATA_VALUE:
                continue
            sanitized_items.append(sanitized_item)
        return sanitized_items

    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return _OMIT_METADATA_VALUE
    return value


def _sanitize_metadata_for_json(metadata: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = _coerce_json_metadata_value(metadata)
    if isinstance(sanitized, dict):
        return sanitized
    return {}


__all__ = [
    "PHOTO_ROW_FILTER",
    "_SQLITE_PARAM_CHUNK_SIZE",
    "_sanitize_metadata_for_json",
]
