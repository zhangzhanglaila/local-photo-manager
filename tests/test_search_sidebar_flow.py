"""A search must stay reachable while the user browses other views.

Pressing Enter used to hand the whole content area to the search loading panel;
navigating away now hides that panel and the finished search is announced through
the sidebar entry instead of yanking the view back.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.gui.coordinators.search_coordinator import SearchCoordinator
from iPhoto.gui.services.search_thread import SearchProgress, SearchStage


class _Sidebar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def set_search_state(self, query: str, **kwargs) -> None:
        self.calls.append((query, kwargs))


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_message(self, message: str, timeout: int = 0) -> None:
        self.messages.append(message)


class _Router:
    def __init__(self) -> None:
        self.shown = 0

    def show_gallery(self) -> None:
        self.shown += 1


def _fake_coordinator(*, visible: bool, results: list | None = None):
    coordinator = SimpleNamespace(
        _window=SimpleNamespace(ui=SimpleNamespace(sidebar=_Sidebar())),
        _status_bar=_StatusBar(),
        _view_router=_Router(),
        _search_state={
            "query": "cat",
            "results": list(results or []),
            "done": False,
            "error": "",
            "serial": 7,
        },
        is_search_page_visible=lambda: visible,
        _displayed=[],
        _shown_loading=[],
        _shown_errors=[],
    )
    coordinator.display_search_results_raw = (
        lambda result_list, query: coordinator._displayed.append((list(result_list), query))
    )
    coordinator.show_search_loading = (
        lambda query: coordinator._shown_loading.append(query)
    )
    coordinator.display_search_error = (
        lambda error: coordinator._shown_errors.append(error)
    )
    return coordinator


def testfinish_search_shows_results_when_panel_still_visible() -> None:
    results = [{"asset_id": "a"}]
    coordinator = _fake_coordinator(visible=True, results=results)

    SearchCoordinator.finish_search(coordinator, 7, results, "", "cat")

    assert coordinator._displayed == [(results, "cat")]
    assert coordinator._window.ui.sidebar.calls == [("cat", {"count": 1})]


def testfinish_search_announces_results_after_navigating_away() -> None:
    results = [{"asset_id": "a"}, {"asset_id": "b"}]
    coordinator = _fake_coordinator(visible=False)

    SearchCoordinator.finish_search(coordinator, 7, results, "", "cat")

    assert coordinator._displayed == []
    assert coordinator._window.ui.sidebar.calls == [("cat", {"count": 2})]
    assert coordinator._search_state["done"] is True
    assert any(
        "搜索完成" in message and "2" in message
        for message in coordinator._status_bar.messages
    )


def testfinish_search_ignores_superseded_search() -> None:
    coordinator = _fake_coordinator(visible=True)

    SearchCoordinator.finish_search(
        coordinator, 6, [{"asset_id": "old"}], "", "cat"
    )

    assert coordinator._displayed == []
    assert coordinator._window.ui.sidebar.calls == []


def testfinish_search_reports_failure_away_from_panel() -> None:
    coordinator = _fake_coordinator(visible=False)

    SearchCoordinator.finish_search(coordinator, 7, None, "boom", "cat")

    assert coordinator._window.ui.sidebar.calls == [("cat", {"failed": True})]
    assert any("搜索失败" in message for message in coordinator._status_bar.messages)


def test_search_entry_click_reopens_loading_panel() -> None:
    coordinator = _fake_coordinator(visible=False)

    SearchCoordinator.handle_search_entry_selected(coordinator)

    assert coordinator._shown_loading == ["cat"]
    assert coordinator._view_router.shown == 1


def test_search_entry_click_shows_finished_results() -> None:
    results = [{"asset_id": "a"}]
    coordinator = _fake_coordinator(visible=False, results=results)
    coordinator._search_state["done"] = True

    SearchCoordinator.handle_search_entry_selected(coordinator)

    assert coordinator._displayed == [(results, "cat")]


class _Page:
    def __init__(self) -> None:
        self.model_loading = 0
        self.loading: list[str] = []

    def show_model_loading(self) -> None:
        self.model_loading += 1

    def show_search_loading(self, message: str) -> None:
        self.loading.append(message)


def test_progress_does_not_reopen_panel_after_navigating_away() -> None:
    coordinator = _fake_coordinator(visible=False)
    page = _Page()
    coordinator._window.ui.gallery_page = page

    SearchCoordinator.update_search_progress(
        coordinator, 7, SearchProgress(SearchStage.MODEL, "正在加载 AI 模型...")
    )

    assert page.model_loading == 0
    assert page.loading == []


def test_progress_updates_panel_while_search_visible() -> None:
    coordinator = _fake_coordinator(visible=True)
    page = _Page()
    coordinator._window.ui.gallery_page = page

    SearchCoordinator.update_search_progress(
        coordinator, 7, SearchProgress(SearchStage.MODEL, "正在加载 AI 模型...")
    )

    assert page.model_loading == 1

    SearchCoordinator.update_search_progress(
        coordinator, 7, SearchProgress(SearchStage.SEARCH, "正在搜索...")
    )

    assert page.loading == ["正在搜索..."]


def testcurrent_album_rel_path_only_returns_nested_albums() -> None:
    library = Path("/library")

    nested = SimpleNamespace(
        _context=SimpleNamespace(library=SimpleNamespace(root=lambda: library)),
        _gallery_vm=SimpleNamespace(active_root=SimpleNamespace(value=library / "Trip")),
    )
    root_view = SimpleNamespace(
        _context=SimpleNamespace(library=SimpleNamespace(root=lambda: library)),
        _gallery_vm=SimpleNamespace(active_root=SimpleNamespace(value=library)),
    )

    assert SearchCoordinator.current_album_rel_path(nested) == "Trip"
    assert SearchCoordinator.current_album_rel_path(root_view) is None


def test_search_history_dedupes_and_keeps_ten_entries() -> None:
    class _Settings:
        def __init__(self) -> None:
            self.values: dict[str, object] = {"agent.search_history": ["a", "b"]}

        def get(self, key: str, default=None):
            return self.values.get(key, default)

        def set(self, key: str, value) -> None:
            self.values[key] = value

    settings = _Settings()
    coordinator = SimpleNamespace(_context=SimpleNamespace(settings=settings))

    SearchCoordinator.record_search_history(coordinator, "a")
    SearchCoordinator.record_search_history(coordinator, "c")

    assert settings.values["agent.search_history"] == ["c", "a", "b"]

    for index in range(12):
        SearchCoordinator.record_search_history(coordinator, f"query-{index}")

    history = settings.values["agent.search_history"]
    assert len(history) == 10
    assert history[0] == "query-11"


def test_stale_progress_is_ignored() -> None:
    coordinator = _fake_coordinator(visible=True)
    page = _Page()
    coordinator._window.ui.gallery_page = page

    SearchCoordinator.update_search_progress(
        coordinator, 6, SearchProgress(SearchStage.SEARCH, "正在搜索...")
    )

    assert page.model_loading == 0
    assert page.loading == []

