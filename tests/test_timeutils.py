"""Datetime conversions must not depend on platform ``mktime`` limits.

On Windows ``datetime.timestamp()`` goes through ``mktime``, which raises
``OSError: [Errno 22]`` for values around the epoch (1970-01-01 in UTC+8, for
example). A photo dated that early crashed the scanner's batch, so the helpers in
``iPhoto.utils.timeutils`` convert arithmetically instead.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from iPhoto.utils.timeutils import to_epoch_microseconds, to_epoch_seconds


def test_epoch_adjacent_datetime_does_not_raise() -> None:
    value = datetime.fromtimestamp(1000)

    # The platform conversion may raise on Windows; the helper must not, and it
    # must agree with the platform conversion where that one works.
    seconds = to_epoch_seconds(value)
    assert seconds == pytest.approx(1000.0, abs=1.0)


def test_microseconds_are_computed_for_old_dates() -> None:
    value = datetime(1970, 1, 1)

    micros = to_epoch_microseconds(value)

    assert micros == int(to_epoch_seconds(value) * 1_000_000)


def test_missing_value_becomes_zero() -> None:
    assert to_epoch_microseconds(None) == 0


def test_aware_datetimes_are_respected() -> None:
    aware = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)

    assert to_epoch_seconds(aware) == pytest.approx(aware.timestamp())


def test_far_future_and_past_values_stay_finite() -> None:
    base = datetime.now()
    for delta in(-timedelta(days=365 * 60), timedelta(days=365 * 60)):
        value = base + delta
        seconds = to_epoch_seconds(value)
        assert isinstance(seconds, float)
        assert abs(seconds) < 1e13


def test_helper_matches_time_time_for_now() -> None:
    now = datetime.now()

    assert to_epoch_seconds(now) == pytest.approx(time.time(), abs=5.0)
