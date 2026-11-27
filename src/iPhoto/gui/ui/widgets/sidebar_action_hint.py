"""In-widget hint shown over the sidebar's header "+" affordance."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel

from ....i18n import tr


class SidebarActionHintMixin:
    """Own the tooltip-like label that advertises the library bind action."""

    def _build_action_hint(self) -> None:
        """Create the in-widget hint shown over the header ＋.

        ``QToolTip`` was used first, but on Windows the native popup follows the
        system colour scheme while its text colour comes from the application
        palette, which produced a black box with dark text that no stylesheet could
        fix. Painting our own label keeps the contrast under our control.
        """

        hint = QLabel(self)
        hint.setObjectName("sidebarActionHint")
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hint.setStyleSheet(
            "QLabel#sidebarActionHint {"
            "  background-color: #FFFFFF;"
            "  color: #1D1D1F;"
            "  border: 1px solid rgba(0, 0, 0, 0.12);"
            "  border-radius: 6px;"
            "  padding: 3px 8px;"
            "  font-size: 12px;"
            "}"
        )
        hint.hide()
        self._action_hint = hint

    def _show_action_hint(self, viewport_pos: QPoint) -> None:
        hint = getattr(self, "_action_hint", None)
        if hint is None:
            return
        hint.setText(tr("action.set_basic_library"))
        hint.adjustSize()

        # Anchor under the pointer but keep the label inside the sidebar.
        anchor = self._tree.viewport().mapTo(self, viewport_pos + QPoint(12, 18))
        x = max(2, min(self.width() - hint.width() - 2, anchor.x()))
        y = max(2, min(self.height() - hint.height() - 2, anchor.y()))
        hint.move(x, y)
        hint.show()
        hint.raise_()

    def _hide_action_hint(self) -> None:
        hint = getattr(self, "_action_hint", None)
        if hint is not None:
            hint.hide()

