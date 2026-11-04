"""Interactive asset grid with click, long-press, and drop handling."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QModelIndex, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QListView

from ....config import LONG_PRESS_THRESHOLD_MS

_IS_DARWIN = sys.platform == "darwin"
_IS_LINUX = sys.platform == "linux"


class AssetGrid(QListView):
    """Grid view that distinguishes between clicks and long presses."""

    # ``QModelIndex`` provides a precise Qt meta-type that aligns with all
    # slots consuming the signal.  Nuitka requires this explicit annotation so
    # it can match the signal signature without falling back to ``object``.
    itemClicked = Signal(QModelIndex)
    itemDoubleClicked = Signal(QModelIndex)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)

    _DRAG_CANCEL_THRESHOLD = 6

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._on_long_press_timeout)
        self._pressed_index: Optional[QModelIndex] = None
        self._press_pos: Optional[QPoint] = None
        self._long_press_active = False
        self._suppress_next_preview_leave = False
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        # The gallery only starts prefetching once the visible range is reported.
        # A longer debounce left stale rows on screen after a fast drag, so keep it
        # short: reporting is cheap unless a window reload is actually needed.
        self._update_timer.setInterval(50)
        self._update_timer.timeout.connect(self._emit_visible_rows)
        self._visible_range: Optional[tuple[int, int]] = None
        self._model = None
        self._model_resetting = False
        self._external_drop_enabled = False
        self._drop_handler: Optional[Callable[[List[Path]], None]] = None
        self._drop_validator: Optional[Callable[[List[Path]], bool]] = None
        self._preview_enabled = True

    # ------------------------------------------------------------------
    # Mouse event handling
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            viewport_pos = self._viewport_pos(event)
            index = self.indexAt(viewport_pos)
            if index.isValid():
                self._pressed_index = index
                self._press_pos = QPoint(viewport_pos)
                self._long_press_active = False
                self._press_timer.stop()
                if self._preview_enabled:
                    self._press_timer.start(LONG_PRESS_THRESHOLD_MS)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._press_pos is not None and not self._long_press_active:
            viewport_pos = self._viewport_pos(event)
            if (viewport_pos - self._press_pos).manhattanLength() > self._DRAG_CANCEL_THRESHOLD:
                self._cancel_pending_long_press()
        elif self._long_press_active and self._pressed_index is not None:
            viewport_pos = self._viewport_pos(event)
            index = self.indexAt(viewport_pos)
            if not index.isValid() or index != self._pressed_index:
                self.previewCancelled.emit()
                self._reset_state()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        was_long_press = self._long_press_active
        index = self._pressed_index
        self._cancel_pending_long_press()
        if event.button() == Qt.MouseButton.LeftButton:
            if was_long_press:
                self.previewReleased.emit()
            elif index is not None and index.isValid() and not self._model_resetting:
                self.itemClicked.emit(index)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        viewport_pos = self._viewport_pos(event)
        index = self.indexAt(viewport_pos)
        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            self._cancel_pending_long_press()
            self.itemDoubleClicked.emit(index)
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._long_press_active:
            if self._should_suppress_preview_leave():
                self._suppress_next_preview_leave = False
                super().leaveEvent(event)
                return
            self.previewCancelled.emit()
        self._cancel_pending_long_press()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self._schedule_visible_rows_update)

    # ------------------------------------------------------------------
    # Preview configuration
    # ------------------------------------------------------------------
    def set_preview_enabled(self, enabled: bool) -> None:
        """Enable or disable the long-press preview workflow."""

        self._preview_enabled = bool(enabled)
        if not self._preview_enabled:
            self._cancel_pending_long_press()

    def preview_enabled(self) -> bool:
        """Return ``True`` when long-press previews are currently allowed."""

        return self._preview_enabled

    # ------------------------------------------------------------------
    # External file drop configuration
    # ------------------------------------------------------------------
    def configure_external_drop(
        self,
        *,
        handler: Optional[Callable[[List[Path]], None]] = None,
        validator: Optional[Callable[[List[Path]], bool]] = None,
    ) -> None:
        """Enable or disable external drop support for the grid view.

        Parameters
        ----------
        handler:
            Callable invoked when a valid drop operation completes.  When
            ``None`` the grid reverts to its default behaviour and external
            drops are ignored entirely.
        validator:
            Optional callable used to preflight an incoming drag.  The
            validator receives the list of candidate file paths and returns
            ``True`` to accept the drag or ``False`` to reject it.  When left
            unspecified every drop that provides at least one local file is
            considered acceptable.
        """

        self._drop_handler = handler
        self._drop_validator = validator
        self._external_drop_enabled = handler is not None
        self.setAcceptDrops(self._external_drop_enabled)
        # The visual items live inside the viewport object, so we must enable drops there
        # as well; otherwise Qt will refuse to deliver drag/drop events to the grid.
        self.viewport().setAcceptDrops(self._external_drop_enabled)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if _IS_LINUX:
            # On Linux with WA_TranslucentBackground, enlarging the window
            # exposes new viewport area that Qt has not yet painted.  The
            # compositor may present the frame before the deferred
            # ``update()`` arrives, producing visible tearing in the
            # newly exposed region.  A synchronous repaint ensures the
            # full viewport is composited from Qt's back buffer before
            # the frame is shown — the same double-buffering strategy
            # used in ``scrollContentsBy()``.
            self.viewport().repaint()
        self._schedule_visible_rows_update()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # type: ignore[override]
        if _IS_LINUX:
            # Double-buffering strategy for Linux with WA_TranslucentBackground:
            #
            # The default ``super().scrollContentsBy()`` calls the C++
            # ``viewport()->scroll(dx, dy)`` which triggers a pixel-shift
            # blit.  Under ARGB visuals (both X11 and Wayland) this blit
            # races with the compositor and produces visible tearing or
            # checkerboard artefacts.
            #
            # We skip the super call entirely so the blit never happens.
            # The scrollbar position has *already* been updated by the time
            # this method is called (by ``QAbstractScrollArea``), so the
            # next ``paintEvent`` will render all items at their correct
            # offsets.
            #
            # Skipping super() also skips the ``doDelayedItemsLayout()``
            # call that ``QListView::scrollContentsBy()`` normally performs.
            # After a resize, subclasses like ``GalleryGridView`` update
            # icon/grid sizes which schedules a delayed items layout.  If
            # the user scrolls before that layout fires, the viewport would
            # be repainted with stale item positions, producing checkerboard
            # artefacts.  Flushing the pending layout first ensures item
            # geometry is up-to-date before the synchronous repaint.
            self.executeDelayedItemsLayout()
            self.viewport().repaint()
        else:
            super().scrollContentsBy(dx, dy)
        self._schedule_visible_rows_update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if not self._external_drop_enabled:
            super().dragEnterEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # type: ignore[override]
        if not self._external_drop_enabled:
            super().dragMoveEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        if not self._external_drop_enabled or self._drop_handler is None:
            super().dropEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._drop_handler(paths)

    def setModel(self, model) -> None:  # type: ignore[override]
        if self._model is not None:
            try:
                self._model.modelAboutToBeReset.disconnect(self._on_model_about_to_reset)
            except (RuntimeError, TypeError):
                pass
            try:
                self._model.modelReset.disconnect(self._on_model_reset_done)
            except (RuntimeError, TypeError):
                pass
            try:
                self._model.modelReset.disconnect(self._schedule_visible_rows_update)
            except (RuntimeError, TypeError):
                pass
            try:
                self._model.rowsInserted.disconnect(self._schedule_visible_rows_update)
            except (RuntimeError, TypeError):
                pass
            try:
                self._model.rowsRemoved.disconnect(self._schedule_visible_rows_update)
            except (RuntimeError, TypeError):
                pass
        super().setModel(model)
        self._model = model
        self._model_resetting = False
        if model is not None:
            model.modelAboutToBeReset.connect(self._on_model_about_to_reset)
            model.modelReset.connect(self._on_model_reset_done)
            model.modelReset.connect(self._schedule_visible_rows_update)
            model.rowsInserted.connect(self._schedule_visible_rows_update)
            model.rowsRemoved.connect(self._schedule_visible_rows_update)
        self._schedule_visible_rows_update()

    def _on_model_about_to_reset(self) -> None:
        self._model_resetting = True
        self._update_timer.stop()
        # Cancel the timer so we don't start a new preview during the reset.
        self._press_timer.stop()
        # The QModelIndex becomes invalid after reset, clear it.
        self._pressed_index = None
        self._press_pos = None
        # Keep _long_press_active intact — mouseReleaseEvent needs it to
        # emit previewReleased so the preview popup can close.

    def _on_model_reset_done(self) -> None:
        # Defer clearing the flag so views finish processing the reset
        # before we allow indexAt()/visualRect() calls.
        QTimer.singleShot(50, self._clear_model_resetting)

    def _clear_model_resetting(self) -> None:
        self._model_resetting = False
        self._schedule_visible_rows_update()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cancel_pending_long_press(self) -> None:
        self._press_timer.stop()
        self._reset_state()

    def _reset_state(self) -> None:
        self._long_press_active = False
        self._pressed_index = None
        self._press_pos = None
        self._suppress_next_preview_leave = False

    def _on_long_press_timeout(self) -> None:
        if not self._preview_enabled or self._model_resetting:
            self._reset_state()
            return
        if self._pressed_index is not None and self._pressed_index.isValid():
            self._long_press_active = True
            self._suppress_next_preview_leave = _IS_DARWIN
            self.requestPreview.emit(self._pressed_index)

    def _should_suppress_preview_leave(self) -> bool:
        if not _IS_DARWIN or not self._suppress_next_preview_leave:
            return False
        buttons = QApplication.mouseButtons()
        return bool(buttons & Qt.MouseButton.LeftButton)

    def _schedule_visible_rows_update(self) -> None:
        if self._model_resetting:
            return
        self._update_timer.start()

    def _viewport_pos(self, event: QMouseEvent) -> QPoint:
        """Return the event position mapped into viewport coordinates."""

        viewport = self.viewport()

        def _validated(point: Optional[QPoint]) -> Optional[QPoint]:
            if point is None:
                return None
            if viewport.rect().contains(point):
                return point
            return None

        if hasattr(event, "position"):
            candidate = _validated(event.position().toPoint())
            if candidate is not None:
                return candidate

        if hasattr(event, "pos"):
            candidate = _validated(event.pos())
            if candidate is not None:
                return candidate

        global_point: Optional[QPoint] = None

        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            global_point = global_position().toPoint()
        elif global_position is not None:
            global_point = global_position.toPoint()

        if global_point is None and hasattr(event, "globalPos"):
            global_point = event.globalPos()

        if global_point is not None:
            mapped = viewport.mapFromGlobal(global_point)
            candidate = _validated(mapped)
            if candidate is not None:
                return candidate

        # Fallback for any other exotic QMouseEvent implementations. At this point
        # we have no reliable coordinate system information, so best-effort return
        # of the event's integer components is the safest option.
        return QPoint(event.x(), event.y())

    # Kept as a fraction of the platform wheel step. The grid scrolls per pixel,
    # so one notch used to move three lines, which in a dense grid jumped several
    # rows at a time. Halving the step keeps a notch close to one row while
    # leaving the rest of the platform semantics (horizontal wheel, ctrl-zoom)
    # untouched.
    WHEEL_STEP_RATIO = 0.5

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        delta = event.angleDelta()
        if delta.y() == 0 or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return

        scaled = QPoint(
            int(round(delta.x() * self.WHEEL_STEP_RATIO)),
            int(round(delta.y() * self.WHEEL_STEP_RATIO)),
        )
        if scaled.x() == 0 and delta.x() != 0:
            scaled.setX(1 if delta.x() > 0 else -1)
        if scaled.y() == 0:
            scaled.setY(1 if delta.y() > 0 else -1)

        reduced = QWheelEvent(
            event.position(),
            event.globalPosition(),
            event.pixelDelta(),
            scaled,
            event.buttons(),
            event.modifiers(),
            event.phase(),
            event.inverted(),
            event.source(),
        )
        super().wheelEvent(reduced)
        event.accept()

    def _row_near_viewport_edge(self, viewport_rect, *, top: bool) -> int:
        """Return the row nearest a viewport edge, or -1 when nothing is hit.

        Probing the exact corners is unreliable: in icon mode a corner usually
        falls in the spacing between items, ``indexAt`` returns an invalid index
        and the visible range was never reported at all. Without it the gallery
        store cannot keep a cache window around the viewport, so scrolling fell
        back to fetching rows one at a time.
        """

        y = viewport_rect.top() + 2 if top else viewport_rect.bottom() - 2
        candidates = (
            viewport_rect.center().x(),
            viewport_rect.left() + viewport_rect.width() // 4,
            viewport_rect.left() + 2,
            viewport_rect.right() - 2,
        )
        for x in candidates:
            index = self.indexAt(QPoint(x, y))
            if index.isValid():
                return index.row()
        return -1

    def _emit_visible_rows(self) -> None:
        try:
            self._emit_visible_rows_impl()
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "_emit_visible_rows failed", exc_info=True
            )

    def _emit_visible_rows_impl(self) -> None:
        if self._model_resetting:
            return
        model = self.model()
        if model is None:
            return
        try:
            row_count = model.rowCount()
        except Exception:
            return
        if row_count <= 0:
            if self._visible_range is not None:
                self._visible_range = None
            return
        viewport = self.viewport()
        if viewport is None:
            return
        viewport_rect = viewport.rect()
        if viewport_rect.isEmpty():
            return

        # Re-check model state right before accessing C++ internals,
        # since the model could have been invalidated between checks.
        if self._model_resetting:
            return
        try:
            first = self._row_near_viewport_edge(viewport_rect, top=True)
            last = self._row_near_viewport_edge(viewport_rect, top=False)
        except Exception:
            return

        if first == -1 and last == -1:
            return
        if first == -1:
            first = 0
        if last == -1:
            last = row_count - 1

        buffer = 20
        first = max(0, first - buffer)
        last = min(row_count - 1, last + buffer)
        if first > last:
            return

        visible_range = (first, last)
        if self._visible_range == visible_range:
            return

        self._visible_range = visible_range
        self.visibleRowsChanged.emit(first, last)

    def _extract_local_files(self, event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> List[Path]:
        """Return all unique local file paths advertised by *event*.

        The helper normalises the reported URLs, discards remote resources, and
        guarantees deterministic ordering so validators can rely on stable
        inputs.  ``Path.resolve`` is intentionally avoided here to keep the
        method lightweight; callers that require canonical paths can resolve
        them as needed.
        """

        mime = event.mimeData()
        if mime is None:
            return []
        urls = getattr(mime, "urls", None)
        if not callable(urls):
            return []
        seen: set[Path] = set()
        paths: List[Path] = []
        for url in urls():
            if not url.isLocalFile():
                continue
            local = Path(url.toLocalFile()).expanduser()
            if local in seen:
                continue
            seen.add(local)
            paths.append(local)
        return paths
