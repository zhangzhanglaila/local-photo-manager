"""Window chrome and drag handling for the floating info panel.

The panel is frameless: this mixin keeps the close-button colours, the label
factory and the title-bar drag/reflow logic out of the content code.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import QLabel, QSizePolicy


class InfoPanelChromeMixin:
    """Extracted from :class:`InfoPanel`."""

    def _apply_close_button_style(self) -> None:
        """Recompute hover/pressed colours from the current palette."""
        text = self.palette().color(QPalette.ColorRole.WindowText)
        hover = QColor(text)
        hover.setAlpha(20)
        pressed = QColor(text)
        pressed.setAlpha(35)
        self._close_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; }"
            f"QToolButton:hover {{ background-color: {hover.name(QColor.NameFormat.HexArgb)}; border-radius: 6px; }}"
            f"QToolButton:pressed {{ background-color: {pressed.name(QColor.NameFormat.HexArgb)}; border-radius: 6px; }}"
        )



    def _resolve_action_icon_tint(self) -> str | None:
        color = self.palette().color(QPalette.ColorRole.ButtonText)
        if not color.isValid():
            color = self.palette().color(QPalette.ColorRole.WindowText)
        if not color.isValid():
            return None
        return color.name(QColor.NameFormat.HexArgb)



    def _make_content_label(self) -> QLabel:
        """Create a word-wrapped plain-text label with stable vertical sizing."""

        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        return label



    def _refresh_panel_geometry(self, *, recenter: bool = False) -> None:
        """Recompute the preferred panel geometry after content changes."""

        self.ensurePolished()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        # Qt's adjustSize() for top-level windows passes sizeHint().width() into
        # totalHeightForWidth().  That width varies with text content, so sparse
        # and rich metadata can accidentally produce the same total height.
        # QLabel.updateGeometry() also skips parent-layout invalidation when the
        # panel is hidden (isVisible() is False), leaving child layout caches
        # stale.  Use the actual widget width instead — totalHeightForWidth()
        # calls label.heightForWidth() directly, which is always fresh.
        w = max(self.width(), self.minimumWidth())
        if layout is not None and layout.hasHeightForWidth():
            h = layout.totalHeightForWidth(w)
            self.resize(w, h)
        else:
            self.adjustSize()
            target_size = self.sizeHint().expandedTo(self.minimumSize())
            if target_size.isValid():
                self.resize(target_size)
        if recenter:
            self._center_over_parent()



    def _refresh_or_schedule_panel_geometry(self, *, recenter: bool = False) -> None:
        """Refresh hidden layouts immediately, but coalesce visible reflows."""

        if self.isVisible():
            self._schedule_post_show_reflow(recenter=recenter)
            return
        self._refresh_panel_geometry(recenter=recenter)



    def _center_over_parent(self) -> None:
        """Center the panel over its parent using the current widget size."""

        parent = self.parentWidget()
        if parent is None or not parent.isVisible():
            return
        center = parent.geometry().center()
        self.move(
            center.x() - self.width() // 2,
            center.y() - self.height() // 2,
        )



    def _schedule_post_show_reflow(self, *, recenter: bool) -> None:
        """Queue a deferred geometry reflow after show or visible content updates."""

        self._post_show_reflow_recenter = self._post_show_reflow_recenter or recenter
        if self._post_show_reflow_queued:
            return
        self._post_show_reflow_queued = True
        QTimer.singleShot(0, self._run_post_show_reflow)



    def _run_post_show_reflow(self) -> None:
        """Finalize the layout once the initial show event has fully settled."""

        self._post_show_reflow_queued = False
        recenter = self._post_show_reflow_recenter
        self._post_show_reflow_recenter = False
        self._refresh_panel_geometry(recenter=recenter)
        self.update()



    def _try_start_system_drag(self) -> bool:
        """Ask the window manager to move the frameless tool window if possible."""

        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemMove())
        except RuntimeError:
            return False



    def _begin_drag(self, event: QMouseEvent) -> bool:
        """Start a native or manual drag from a title-bar mouse press."""

        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._try_start_system_drag():
            self._drag_active = False
            self._drag_offset = None
            return True
        self._drag_active = True
        self._drag_offset = (
            event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        )
        return True



    def _continue_drag(self, event: QMouseEvent) -> bool:
        """Advance a manual drag if one is active."""

        if not self._drag_active:
            return False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._drag_active = False
            self._drag_offset = None
            return True
        if self._drag_offset is None:
            return True
        target = event.globalPosition().toPoint() - self._drag_offset
        self.move(target)
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.setPosition(target)
            except RuntimeError:
                pass
        return True



    def _end_drag(self) -> bool:
        """Terminate a manual drag session."""

        if not self._drag_active:
            return False
        self._drag_active = False
        self._drag_offset = None
        return True

