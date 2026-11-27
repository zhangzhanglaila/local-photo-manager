"""Present AI-index progress: gallery page + status bar, copy from i18n.

The worker reports what it is doing; this module decides what the user sees.
Both used to live inside ``MainCoordinator`` — the four-argument status callback
and its Chinese copy sat in the coordinator, so the coordinator grew a second
job and the strings could not be translated.

Nothing here is on the gallery hot path: it runs while a search model is loaded
or while the semantic index is (re)built.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QTimer

from iPhoto.i18n import tr

_LOGGER = logging.getLogger(__name__)

#: Stages that only need "the model is on its way" feedback.
_MODEL_STAGES = frozenset({"loading_model", "model_loading", "model_loaded", "counting"})


class AiIndexStatus:
    """The kinds of progress an AI-index worker reports."""

    LOADING_MODEL = "loading_model"
    MODEL_LOADING = "model_loading"
    MODEL_LOADED = "model_loaded"
    COUNTING = "counting"
    STARTING = "starting"
    PROGRESS = "progress"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class AiIndexStatusReport:
    """One progress update from an AI-index worker."""

    kind: str
    current: int = 0
    total: int = 0
    message: str = ""


class AiIndexStatusPresenter:
    """Render :class:`AiIndexStatusReport` onto the gallery page and status bar."""

    def __init__(self, page, status_bar) -> None:
        self._page = page
        self._status_bar = status_bar

    def show_intro(self) -> None:
        """Explain why the first search waits: it is building the index."""

        self._page.show_loading_message(
            tr("ai.index.intro_title"),
            tr("ai.index.intro_detail"),
            show_progress=True,
        )

    def apply(self, report: AiIndexStatusReport) -> None:
        """Render *report*. Must run on the GUI thread."""

        _LOGGER.info("AI index status: %s (%s/%s)", report.kind, report.current, report.total)
        page = self._page
        kind = report.kind

        if kind in _MODEL_STAGES:
            page.show_model_loading()
        elif kind == AiIndexStatus.STARTING:
            page.show_indexing_progress(0, report.current)
        elif kind == AiIndexStatus.PROGRESS:
            page.show_indexing_progress(report.current, report.total)
            percent = (
                int(report.current / report.total * 100) if report.total > 0 else 0
            )
            self._status_bar.show_message(
                tr(
                    "ai.index.progress",
                    current=report.current,
                    total=report.total,
                    percent=percent,
                )
            )
        elif kind == AiIndexStatus.DONE:
            page.show_done(report.current)
            if report.current > 0:
                self._status_bar.show_message(
                    tr("ai.index.done", count=report.current), 5000
                )
            else:
                self._status_bar.show_message(tr("ai.index.up_to_date"), 3000)
        elif kind == AiIndexStatus.ERROR:
            page.show_error(tr("ai.index.error_title"), report.message)
            self._status_bar.show_message(
                tr("ai.index.error_status", message=report.message), 10000
            )

    def reporter(self) -> Callable[[AiIndexStatusReport], None]:
        """Return the callback handed to a worker.

        Workers run on a pool thread, so each report is queued back onto the GUI
        thread before anything touches a widget.
        """

        def report(update: AiIndexStatusReport) -> None:
            QTimer.singleShot(0, lambda: self.apply(update))

        return report


__all__ = [
    "AiIndexStatus",
    "AiIndexStatusPresenter",
    "AiIndexStatusReport",
]
