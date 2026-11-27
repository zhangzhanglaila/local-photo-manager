"""Semantic-search flow: sidebar entry, loading panel, results and history.

The search state machine used to live inside ``MainCoordinator``: 400 lines of
thread lifecycle, panel switching, history persistence and popup thumbnails in
the same object that also wires menus and the gallery. It only needs the window,
the gallery view model/store, the view router, the status bar and the thumbnail
service, so it now owns that state itself and ``MainCoordinator`` merely creates
it and connects its signals.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSize
from PySide6.QtGui import QAction

from iPhoto.application.dtos import ScanStatus
from iPhoto.gui.services.search_thread import (
    SearchProgress,
    SearchStage,
    SearchThread,
)
from iPhoto.i18n import tr

#: Search workers that were still running when the app shut down. Keeping them
#: referenced prevents a live QThread from being destroyed during teardown.
_ORPHANED_SEARCH_THREADS: list = []


class SearchCoordinator(QObject):
    """Owns semantic-search requests and the UI state that mirrors them."""

    def __init__(
        self,
        window,
        context,
        *,
        gallery_vm,
        gallery_store,
        view_router,
        status_bar,
        thumbnail_service,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._context = context
        self._gallery_vm = gallery_vm
        self._gallery_store = gallery_store
        self._view_router = view_router
        self._status_bar = status_bar
        self._thumbnail_service = thumbnail_service
        self._logger = logging.getLogger(__name__)

        # Active semantic search: query, results, completion state and a serial so
        # a superseded search cannot overwrite the newer one.
        self._search_state: dict | None = None
        self._search_serial = 0
        # Running search threads. The previous one must stay referenced until it
        # finishes: replacing the only reference destroyed a live QThread.
        self._search_threads: list = []
        self._search_thread = None
        self._popup_similar_paths: dict[Path, str] = {}

    # -- lifecycle -----------------------------------------------------------
    def shutdown(self) -> None:
        """Stop search workers before their Qt parents are torn down."""

        for thread in list(self._search_threads):
            if thread.isRunning():
                thread.requestInterruption()
                thread.wait(3000)
            if thread.isRunning():
                _ORPHANED_SEARCH_THREADS.append(thread)
        self._search_threads.clear()

    # -- helpers -------------------------------------------------------------
    def _active_session(self):
        return getattr(self._context, "library_session", None)

    def _active_search_spec(self):
        """Search model currently selected, as recorded by bootstrap."""

        return self._context.model_selection.spec("search")

    # -- history and popup ---------------------------------------------------
    def record_search_history(self, query: str) -> None:
        """Remember the query so the search dropdown can offer it again."""

        text = (query or "").strip()
        if not text:
            return
        try:
            history = list(self._context.settings.get("agent.search_history", []) or [])
        except Exception:
            history = []
        history = [text] + [item for item in history if item != text]
        self._context.settings.set("agent.search_history", history[:10])

    def clear_search_history(self) -> None:
        self._context.settings.set("agent.search_history", [])
        self._window.ui.main_header.set_search_history([])

    def current_album_rel_path(self) -> str | None:
        """Library-relative path of the album being viewed, if any."""

        root = self._context.library.root()
        active = getattr(self._gallery_vm, "active_root", None)
        active_path = getattr(active, "value", active)
        if root is None or active_path is None:
            return None
        try:
            relative = (
                Path(active_path).resolve().relative_to(Path(root).resolve()).as_posix()
            )
        except (OSError, ValueError):
            return None
        return None if relative in ("", ".") else relative

    def current_album_label(self) -> str | None:
        relative = self.current_album_rel_path()
        return Path(relative).name if relative else None

    def prepare_search_popup(self) -> None:
        """Fill the dropdown with history, scope and visual shortcuts."""

        header = self._window.ui.main_header
        try:
            history = list(self._context.settings.get("agent.search_history", []) or [])
        except Exception:
            history = []
        header.set_search_history(history[:10])
        header.set_album_scope(self.current_album_label())
        self._populate_popup_similar()

    def _populate_popup_similar(self) -> None:
        """Offer a few thumbnails from the current view for "find similar"."""

        header = self._window.ui.main_header
        size = QSize(72, 72)
        self._popup_similar_paths = {}
        items: list[tuple[str, object]] = []

        store = self._gallery_store
        try:
            count = min(8, store.count())
        except Exception:
            count = 0

        for row in range(count):
            try:
                asset = store.asset_at(row)
            except Exception:
                asset = None
            if asset is None or asset.abs_path is None:
                continue
            asset_id = str(getattr(asset, "id", "") or "")
            if not asset_id:
                continue
            path = Path(asset.abs_path)
            self._popup_similar_paths[path] = asset_id
            pixmap = self._thumbnail_service.get_thumbnail(path, size)
            if pixmap is not None:
                items.append((asset_id, pixmap))

        header.set_similar_photos(items)

    def on_popup_thumbnail_ready(self, path: Path) -> None:
        """Fill a suggested thumbnail as soon as the cache produces it."""

        target = Path(path)
        asset_id = self._popup_similar_paths.get(target)
        if not asset_id:
            return
        header = self._window.ui.main_header
        popup = getattr(header, "_search_popup", None)
        if popup is None or not popup.isVisible():
            return
        pixmap = self._thumbnail_service.get_thumbnail(target, QSize(72, 72))
        if pixmap is not None:
            header.add_similar_photo(asset_id, pixmap)

    def handle_similar_search(self, asset_id: str) -> None:
        """Search for photos that look like *asset_id*."""

        self.handle_search_requested(
            tr("search.similar_query"), "all", similar_to=asset_id
        )

    # -- scan status ---------------------------------------------------------
    def on_ocr_scan_status(self, status: object) -> None:
        """Surface the OCR backfill in the status bar.

        Skipped while the user is looking at search results so the result count
        is not replaced by background-scan chatter. The scan layer sends a
        :class:`ScanStatus` key instead of a finished sentence, so "this is the
        last message" comes from the payload rather than from searching the text
        for a word like ``完成`` (which broke as soon as the UI was translated).
        """

        if not isinstance(status, ScanStatus) or not status.key:
            return
        if self.is_search_page_visible():
            return
        message = tr(status.key, **dict(status.params))
        if status.completed:
            self._status_bar.show_message(message, 5000)
        else:
            self._status_bar.show_message(message)

    # -- search request ------------------------------------------------------
    def handle_search_requested(
        self, query: str, scope: str = "all", *, similar_to: str | None = None
    ) -> None:
        """Handle semantic search request from the search input."""
        self._logger.info("Search requested: %s (scope=%s)", query, scope)

        state = self._search_state
        if (
            state is not None
            and not state.get("done")
            and state.get("query") == query
            and state.get("similar_to") == similar_to
        ):
            # Same query is already searching (e.g. Enter pressed twice): show the
            # panel again instead of spawning a second worker that loads the model
            # in parallel.
            self._view_router.show_gallery()
            self.show_search_loading(query)
            return

        if similar_to is None:
            self.record_search_history(query)

        self._search_serial += 1
        serial = self._search_serial
        self._search_state = {
            "query": query,
            "results": [],
            "done": False,
            "error": "",
            "serial": serial,
            "scope": scope,
            "similar_to": similar_to,
        }
        # A dedicated sidebar entry keeps the search reachable while it loads, so
        # the user can browse other views instead of waiting on the loading panel.
        self._window.ui.sidebar.set_search_state(query, loading=True)

        # Switch to gallery view
        self._view_router.show_gallery()

        # Show loading
        self.show_search_loading(query)

        # Run search in background thread
        from PySide6.QtCore import QTimer

        # Get paths before starting thread
        from iPhoto.agent.services import search_models

        library_root = Path(self._context.library.root())
        model_dir = search_models.model_dir(self._active_search_spec())
        # Reuse the session's embedding service: it is warmed before the face scan
        # starts, so rebuilding a service here would reload the weights on every
        # single search.
        library_session = self._active_session()

        def on_finished(results, error):
            QTimer.singleShot(
                0, lambda: self.finish_search(serial, results, error, query)
            )

        def on_progress(progress):
            QTimer.singleShot(
                0,
                lambda: self.update_search_progress(serial, progress),
            )

        thread = SearchThread(
            query,
            model_dir,
            library_root,
            self._active_search_spec(),
            library_session=library_session,
            precision=self.search_precision(),
            scope_album_path=(
                self.current_album_rel_path() if scope == "album" else None
            ),
            similar_to=similar_to,
        )
        thread.finished.connect(on_finished)
        thread.progress.connect(on_progress)
        thread.finished.connect(lambda *_: self.retire_search_thread(thread))
        self._search_thread = thread
        self._search_threads.append(thread)
        thread.start()

    def retire_search_thread(self, thread) -> None:
        """Release a finished search worker without destroying a running one."""

        thread.wait(1000)
        try:
            self._search_threads.remove(thread)
        except ValueError:
            pass
        if self._search_thread is thread:
            self._search_thread = None
        thread.deleteLater()

    # -- results rendering ---------------------------------------------------
    def display_search_results_raw(self, results: list, query: str) -> None:
        """Display search results."""
        ui = self._window.ui
        # Search results live in the grid; make sure it is the view the page
        # returns to, even when the search was started from the Home view.
        ui.gallery_page.show_grid()
        ui.gallery_page.hide_loading()
        if not results:
            # Order matters: paint the "no matches" state first so a failure in
            # the model update cannot leave the previous search's photos visible.
            ui.gallery_page.show_search_results_header(query, 0)
            ui.gallery_page.show_search_empty(query)
            try:
                self._gallery_vm.show_search_results([])
            except Exception:
                self._logger.debug("could not clear search results", exc_info=True)
            self._status_bar.show_message(tr("search.empty.title", query=query))
            return

        # Show search results header with back button
        total_count = len(results)
        ui.gallery_page.show_search_results_header(query, total_count)
        asset_ids = [r["asset_id"] for r in results]
        self._gallery_vm.show_search_results(asset_ids)

        # Scroll to top
        ui.grid_view.scrollToTop()
        self._status_bar.show_message(
            tr("search.results.found", count=total_count, query=query)
        )

    def finish_search(self, serial: int, results, error: str, query: str) -> None:
        """Complete a search and decide whether to show or announce the results."""

        state = self._search_state
        if (
            state is None
            or state.get("serial") != serial
            or state.get("query") != query
        ):
            return  # superseded by a newer search

        state["done"] = True
        state["error"] = error or ""
        state["results"] = list(results or [])

        sidebar = self._window.ui.sidebar
        if error:
            sidebar.set_search_state(query, failed=True)
        else:
            sidebar.set_search_state(query, count=len(state["results"]))

        if self.is_search_page_visible():
            if error:
                self.display_search_error(error)
            else:
                self.display_search_results_raw(state["results"], query)
            return

        # The user navigated away while the search was running: keep the result
        # available in the sidebar and announce it instead of switching views.
        toast = getattr(self._window.ui, "notification_toast", None)
        if error:
            self._status_bar.show_message(f"搜索失败：{error}", 8000)
        elif state["results"]:
            self._status_bar.show_message(
                f"搜索完成：找到 {len(state['results'])} 张，"
                f"点击左侧「搜索：{query}」查看",
                10000,
            )
            if toast is not None:
                toast.show_toast(f"搜索完成 · {len(state['results'])} 张")
        else:
            self._status_bar.show_message(tr("search.results.none", query=query), 8000)
            if toast is not None:
                toast.show_toast("搜索完成 · 0 张")

    def handle_search_entry_selected(self) -> None:
        """Open the active search (loading panel or its results) from the sidebar."""

        state = self._search_state
        if state is None:
            return
        # The search panel and its results belong to the grid view; leaving Home
        # as the page's base view would hide them once loading finishes.
        page = getattr(self._window.ui, "gallery_page", None)
        if page is not None and hasattr(page, "show_grid"):
            page.show_grid()
        self._view_router.show_gallery()
        if not state.get("done"):
            self.show_search_loading(state["query"])
            return
        error = state.get("error") or ""
        if error:
            self.display_search_error(error)
            return
        self.display_search_results_raw(state.get("results") or [], state["query"])

    def is_search_page_visible(self) -> bool:
        """Return whether the gallery is currently showing the search panel."""

        page = getattr(self._window.ui, "gallery_page", None)
        stack = getattr(page, "_stack", None)
        loading = getattr(page, "_loading_widget", None)
        return (
            stack is not None
            and loading is not None
            and stack.currentWidget() is loading
        )

    def show_search_loading(
        self, query: str, sub_message: str = None, show_continue: bool = False
    ) -> None:
        """Show loading state in the grid view area."""
        self._window.ui.gallery_page.show_search_loading(query)

    def update_search_progress(self, serial: int, progress: SearchProgress) -> None:
        """Update search progress in the UI.

        The worker reports which stage it is in; the panel used to guess it from the
        message text, which broke as soon as the copy was translated.
        """

        state = self._search_state
        if state is None or state.get("serial") != serial:
            return
        if not self.is_search_page_visible():
            # The user navigated away; do not pull them back into the search panel.
            return
        page = self._window.ui.gallery_page
        if progress.stage in (SearchStage.MODEL, SearchStage.INDEX):
            page.show_model_loading()
        else:
            page.show_search_loading(progress.message)

    def display_search_results(self, results: list, query: str = "") -> None:
        """Display search results in the grid view."""
        ui = self._window.ui
        ui.gallery_page.hide_loading()
        if not results:
            self._status_bar.show_message(
                tr("search.results.none_short", query=query)
            )
            return

        asset_ids = [r.asset_id for r in results]
        self._gallery_vm.show_search_results(asset_ids)
        self._status_bar.show_message(
            tr("search.results.found", count=len(results), query=query)
        )

    def display_search_error(self, error: str) -> None:
        """Display search error in the grid view."""
        ui = self._window.ui
        ui.gallery_page.show_error(tr("search.error.title"), error)
        self._status_bar.show_message(
            tr("search.error.status", message=error), 10000
        )

    def on_search_back(self) -> None:
        """Handle back button click during search."""
        # Hide search results header
        ui = self._window.ui
        self._search_state = None
        ui.sidebar.clear_search()
        ui.gallery_page.hide_search_results_header()
        # Return to normal gallery view
        self._gallery_vm.open_all_photos()
        self._status_bar.show_message("")

    # -- precision -----------------------------------------------------------
    def search_precision(self) -> str:
        """Return the configured search precision tier."""

        value = self._context.settings.get("agent.search_precision", "balanced")
        return value if value in {"strict", "balanced", "loose"} else "balanced"

    def handle_search_precision_changed(self, action: QAction) -> None:
        """Persist the precision tier picked from the settings menu."""

        tier = action.data()
        if tier in {"strict", "balanced", "loose"}:
            self._context.settings.set("agent.search_precision", tier)


__all__ = ["SearchCoordinator"]
