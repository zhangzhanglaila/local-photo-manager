"""System tray presence.

Mirrors the reference application: the app keeps a tray icon while it runs, and
right-clicking it offers opening the window, opening settings, and quitting.
Hiding the window (the "minimise" answer on close) keeps the process alive, so
the tray entry is the way back.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ....i18n import tr


class AppTrayIcon(QObject):
    """Thin wrapper around :class:`QSystemTrayIcon` with the app's menu."""

    show_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget, icon: QIcon, tooltip: str = "iPhoto") -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, parent)
        self._tray.setToolTip(tooltip)

        # The menu must stay referenced: a ``QMenu`` owned only by the tray icon
        # can be collected before the user opens it.
        self._menu = QMenu()
        self._menu.addAction(tr("tray.open"), self.show_requested.emit)
        self._menu.addAction(tr("tray.settings"), self.settings_requested.emit)
        self._menu.addSeparator()
        self._menu.addAction(tr("tray.quit"), self.quit_requested.emit)
        self._tray.setContextMenu(self._menu)

        self._tray.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def set_icon(self, icon: QIcon) -> None:
        self._tray.setIcon(icon)

    def show_message(self, title: str, message: str) -> None:
        if QSystemTrayIcon.supportsMessages():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)

    def is_visible(self) -> bool:
        return self._tray.isVisible()

    # ------------------------------------------------------------------
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()


__all__ = ["AppTrayIcon"]
