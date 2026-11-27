"""AI-index progress reaches the gallery page from the GUI thread, in i18n copy."""

from __future__ import annotations

import time

import pytest

from iPhoto.gui.services.ai_index_status import (
    AiIndexStatus,
    AiIndexStatusPresenter,
    AiIndexStatusReport,
)
from iPhoto.i18n import tr


class _Page:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def show_loading_message(self, *args, **kwargs) -> None:
        self.calls.append(("loading_message", args, kwargs))

    def show_model_loading(self) -> None:
        self.calls.append(("model_loading",))

    def show_indexing_progress(self, current: int, total: int) -> None:
        self.calls.append(("indexing_progress", current, total))

    def show_done(self, count: int) -> None:
        self.calls.append(("done", count))

    def show_error(self, title: str, message: str) -> None:
        self.calls.append(("error", title, message))


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int | None]] = []

    def show_message(self, message: str, timeout_ms: int | None = None) -> None:
        self.messages.append((message, timeout_ms))


@pytest.fixture
def page() -> _Page:
    return _Page()


@pytest.fixture
def status_bar() -> _StatusBar:
    return _StatusBar()


@pytest.fixture
def presenter(page: _Page, status_bar: _StatusBar) -> AiIndexStatusPresenter:
    return AiIndexStatusPresenter(page, status_bar)


def test_intro_explains_the_first_index_build(presenter, page) -> None:
    presenter.show_intro()

    kind, args, kwargs = page.calls[0]
    assert kind == "loading_message"
    assert args == (tr("ai.index.intro_title"), tr("ai.index.intro_detail"))
    assert kwargs == {"show_progress": True}


@pytest.mark.parametrize(
    "kind",
    [
        AiIndexStatus.LOADING_MODEL,
        AiIndexStatus.MODEL_LOADING,
        AiIndexStatus.MODEL_LOADED,
        AiIndexStatus.COUNTING,
    ],
)
def test_model_stages_show_the_model_loading_surface(presenter, page, kind) -> None:
    presenter.apply(AiIndexStatusReport(kind))

    assert page.calls == [("model_loading",)]


def test_starting_shows_progress_at_zero(presenter, page) -> None:
    presenter.apply(AiIndexStatusReport(AiIndexStatus.STARTING, 42, 42))

    assert page.calls == [("indexing_progress", 0, 42)]


def test_progress_updates_the_page_and_the_status_bar(presenter, page, status_bar) -> None:
    presenter.apply(AiIndexStatusReport(AiIndexStatus.PROGRESS, 3, 10))

    assert page.calls == [("indexing_progress", 3, 10)]
    message, timeout = status_bar.messages[0]
    assert message == tr("ai.index.progress", current=3, total=10, percent=30)
    assert "3/10" in message and "30%" in message
    assert timeout is None


def test_done_reports_the_processed_count(presenter, page, status_bar) -> None:
    presenter.apply(AiIndexStatusReport(AiIndexStatus.DONE, 12, 0))

    assert page.calls == [("done", 12)]
    message, timeout = status_bar.messages[0]
    assert message == tr("ai.index.done", count=12)
    assert timeout == 5000


def test_done_with_nothing_to_do_says_the_index_is_current(presenter, page, status_bar) -> None:
    presenter.apply(AiIndexStatusReport(AiIndexStatus.DONE, 0, 0))

    assert page.calls == [("done", 0)]
    assert status_bar.messages == [(tr("ai.index.up_to_date"), 3000)]


def test_error_surfaces_the_message_on_both_surfaces(presenter, page, status_bar) -> None:
    presenter.apply(AiIndexStatusReport(AiIndexStatus.ERROR, message="boom"))

    assert page.calls == [("error", tr("ai.index.error_title"), "boom")]
    message, timeout = status_bar.messages[0]
    assert message == tr("ai.index.error_status", message="boom")
    assert timeout == 10000


def test_unknown_kinds_change_nothing(presenter, page, status_bar) -> None:
    presenter.apply(AiIndexStatusReport("something-new", 1, 2))

    assert page.calls == []
    assert status_bar.messages == []


def test_reporter_defers_until_the_event_loop_runs(qapp, presenter, page) -> None:
    """The worker calls this from a pool thread; widgets must not be touched there."""

    report = presenter.reporter()

    report(AiIndexStatusReport(AiIndexStatus.PROGRESS, 1, 2))

    assert page.calls == [], "the report must be queued, not applied inline"

    deadline = time.monotonic() + 5
    while not page.calls and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)

    assert page.calls == [("indexing_progress", 1, 2)]
