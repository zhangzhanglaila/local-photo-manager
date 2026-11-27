"""Constants shared by the playback coordinator and its mixins.

They live here instead of in ``playback_coordinator.py`` because the mixins need
them and importing the coordinator back would be a cycle.
"""

from __future__ import annotations

_INFO_PANEL_METADATA_CACHE_MAX = 200
_LOCATION_SEARCH_RESULT_LIMIT = 5
_LOCATION_SEARCH_DEBOUNCE_MS = 80
_LOCATION_EXTENSION_PROMPT = "map.install_extension_prompt"
_LOCATION_EXIFTOOL_LIMITED_TITLE = "map.feature_limited"
_LOCATION_EXIFTOOL_LIMITED_MESSAGE = "map.location_saved_no_exiftool"
_LOCATION_FILE_WRITE_LIMITED_TITLE = "map.file_write_failed"
_LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE = "map.location_saved_file_write_failed_reason"


__all__ = [name for name in dir() if name.startswith("_") and name.isupper()]
