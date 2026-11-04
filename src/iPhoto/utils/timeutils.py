"""Datetime conversion helpers that behave the same on every platform.

``datetime.timestamp()`` interprets a naive value in local time through the C
``mktime``. On Windows that call rejects values around the epoch, so a photo
dated 1970-01-01 in a UTC+8 timezone raised ``OSError: [Errno 22]`` and took the
scanner's batch down with it. These helpers fall back to plain arithmetic, which
gives the same answer the failing platforms cannot produce.
"""

from __future__ import annotations

import time
from datetime import datetime

_EPOCH = datetime(1970, 1, 1)


def to_epoch_seconds(value: datetime) -> float:
    """Return *value* as seconds since the epoch without relying on ``mktime``.

    Naive values keep the same meaning ``timestamp()`` gives them: local time.
    The fallback therefore applies the standard local offset, ignoring DST at
    the instant in question — the few values that reach it are around the epoch,
    where that difference cannot matter for the one-second tolerance the scanner
    compares against.
    """

    try:
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        pass

    seconds = (value.replace(tzinfo=None) - _EPOCH).total_seconds()
    if value.tzinfo is not None:
        offset = value.utcoffset()
        return seconds - (offset.total_seconds() if offset is not None else 0.0)
    # ``time.timezone`` is the standard offset in seconds west of UTC.
    return seconds + time.timezone


def to_epoch_microseconds(value: datetime | None) -> int:
    """Return *value* as microseconds since the epoch, or ``0`` when missing."""

    if value is None:
        return 0
    return int(to_epoch_seconds(value) * 1_000_000)
