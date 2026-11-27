"""OCR progress reaches the status bar as a structured payload.

The status bar used to decide "this is the final message, auto-expire it" by
searching the rendered text for the word ``完成``. Translating the UI (or
rephrasing the message) silently disabled that branch. The scan layer now sends
:class:`ScanStatus` — an i18n key, its parameters, and an explicit ``completed``
flag — so the decision no longer depends on prose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="the coordinator is a Qt object")

from iPhoto.application.dtos import ScanStatus  # noqa: E402
from iPhoto.gui.coordinators.search_coordinator import SearchCoordinator  # noqa: E402
from iPhoto.i18n import set_language, tr  # noqa: E402


class _StatusBar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def show_message(self, message: str, timeout_ms: int | None = None) -> None:
        self.calls.append((message, timeout_ms))


class _Host:
    def __init__(self, *, search_visible: bool = False) -> None:
        self._status_bar = _StatusBar()
        self._search_visible = search_visible

    def is_search_page_visible(self) -> bool:
        return self._search_visible


def _handle(host: _Host, status: object) -> None:
    SearchCoordinator.on_ocr_scan_status(host, status)


def test_completed_status_auto_expires() -> None:
    host = _Host()

    _handle(host, ScanStatus("status.ocr.done", {"count": 12}, completed=True))

    assert host._status_bar.calls == [(tr("status.ocr.done", count=12), 5000)]


def test_running_status_stays_until_replaced() -> None:
    host = _Host()

    _handle(host, ScanStatus("status.ocr.running"))

    assert host._status_bar.calls == [(tr("status.ocr.running"), None)]


def test_empty_key_clears_without_showing_a_message() -> None:
    host = _Host()

    _handle(host, ScanStatus(""))

    assert host._status_bar.calls == []


def test_status_is_hidden_while_search_results_are_visible() -> None:
    host = _Host(search_visible=True)

    _handle(host, ScanStatus("status.ocr.running"))

    assert host._status_bar.calls == []


def test_status_renders_in_the_active_language() -> None:
    host = _Host()
    set_language("en")
    try:
        _handle(host, ScanStatus("status.ocr.done", {"count": 3}, completed=True))
    finally:
        set_language("zh")

    assert host._status_bar.calls[0][0] == "Text recognition finished (3 processed)"


def test_no_control_flow_on_rendered_text_remains() -> None:
    module = Path(SearchCoordinator.__module__.replace(".", "/") + ".py")
    source = (Path(__file__).resolve().parents[1] / "src" / module).read_text(
        encoding="utf-8"
    )

    assert '"完成" in' not in source
