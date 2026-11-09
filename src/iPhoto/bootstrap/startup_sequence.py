"""Ordered, run-once startup stages.

``gui/main.py`` used to drive startup with a chain of ``_step1``…``_step5`` callbacks
plus ad-hoc boolean flags (``_step5_executed``, ``_scan_finished``) that existed only to
stop a stage from running twice. The ordering lived in the call chain itself, so adding
or reordering work meant editing several closures and hoping no signal fired twice.

``StartupSequence`` keeps the ordering in one declarative list and owns the "run once"
guarantee, leaving stages free to be plain functions.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, Sequence

_LOGGER = logging.getLogger(__name__)

Stage = Callable[[], None]


class StartupSequence:
    """Run startup stages exactly once, in declaration order."""

    def __init__(self, stages: Iterable[Stage]) -> None:
        self._stages: list[Stage] = list(stages)
        self._index = 0
        self._started = False
        self._finished = False

    # ------------------------------------------------------------------ state
    @property
    def started(self) -> bool:
        return self._started

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def position(self) -> int:
        """Index of the next stage to run (``len(stages)`` once finished)."""

        return self._index

    def add(self, stage: Stage) -> None:
        """Append a stage; ignored once the sequence has finished."""

        if self._finished:
            raise RuntimeError("cannot add stages to a finished startup sequence")
        self._stages.append(stage)

    # ------------------------------------------------------------------- run
    def run_next(self) -> bool:
        """Run the next stage and report whether the sequence is now finished."""

        if self._finished:
            return True
        self._started = True
        while self._index < len(self._stages):
            stage = self._stages[self._index]
            self._index += 1
            try:
                stage()
            except Exception:
                # A failing stage must not leave the app half-started in silence: log
                # it with a traceback and stop, mirroring the previous behaviour.
                _LOGGER.exception(
                    "startup stage %d/%d failed", self._index, len(self._stages)
                )
                self._finished = True
                return True
        self._finished = True
        return True


def run_all(stages: Sequence[Stage]) -> None:
    """Convenience for callers that do not need incremental stepping."""

    sequence = StartupSequence(stages)
    while not sequence.run_next():
        pass
