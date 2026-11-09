"""Startup stages run once, in order, and a failure stops the sequence."""

from __future__ import annotations

import pytest

from iPhoto.bootstrap.startup_sequence import StartupSequence, run_all


def test_stages_run_in_order_exactly_once() -> None:
    calls: list[str] = []
    sequence = StartupSequence(
        [
            lambda: calls.append("a"),
            lambda: calls.append("b"),
            lambda: calls.append("c"),
        ]
    )

    sequence.run_next()
    sequence.run_next()
    sequence.run_next()
    sequence.run_next()

    assert calls == ["a", "b", "c"], "each stage runs once, in declaration order"
    assert sequence.finished


def test_a_failing_stage_stops_the_sequence() -> None:
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("stage failed")

    sequence = StartupSequence([lambda: calls.append("a"), boom, lambda: calls.append("c")])

    sequence.run_next()
    sequence.run_next()

    assert calls == ["a"], "stages after the failure do not run"
    assert sequence.finished


def test_stages_cannot_be_added_after_finishing() -> None:
    sequence = StartupSequence([])
    run_all([])

    sequence.run_next()

    with pytest.raises(RuntimeError):
        sequence.add(lambda: None)


def test_run_all_runs_every_stage() -> None:
    calls: list[int] = []

    run_all([lambda: calls.append(1), lambda: calls.append(2)])

    assert calls == [1, 2]
