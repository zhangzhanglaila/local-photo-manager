"""Helpers shared by the edit coordinator and its mixins.

`_video_trim_thumbnail_count` is a pure geometry function and `_APP_LOGGER` is
the trim-specific logger; the video mixin needs both, so they live here instead
of in the coordinator (importing the coordinator back would be a cycle).
"""

from __future__ import annotations

import math

from iPhoto.utils.logging import get_logger

_APP_LOGGER = get_logger().getChild("video_trim")
_DEFAULT_VIDEO_FRAME_STEP_MS = 33


def _video_trim_thumbnail_count(view_width: int, rotation: int, raw_w: int, raw_h: int) -> int:
    """How many thumbnails fill *view_width* for a clip of this geometry.

    Pure function: the caller supplies the geometry recorded when the clip was
    opened, so nothing here can block on a media probe.
    """

    if rotation in {90, 270}:
        display_w, display_h = raw_h, raw_w
    else:
        display_w, display_h = raw_w, raw_h

    logical_thumb_h = 42.0
    logical_thumb_w = 0.0
    if display_w > 0 and display_h > 0:
        logical_thumb_w = display_w * (logical_thumb_h / float(display_h))

    if logical_thumb_w > 1.0:
        count = int(math.ceil(view_width / logical_thumb_w)) + 2
    else:
        count = view_width // max(96, 1)

    return min(max(int(count), 6), 60)


__all__ = ["_APP_LOGGER", "_DEFAULT_VIDEO_FRAME_STEP_MS", "_video_trim_thumbnail_count"]
