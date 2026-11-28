"""Small indeterminate spinner shared by settings and result pages."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QWidget


class Spinner(QWidget):
    """A compact ``Loader2``-style arc.

    Weflow's settings UI uses Lucide's ``Loader2`` at 13–14 px with the current
    text colour and a one-second linear spin. Keeping the same geometry here
    makes the settings rows look calm instead of like a large accent-coloured
    progress ring.
    """

    def __init__(self, diameter: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.stop()
        super().hideEvent(event)

    def _advance(self) -> None:
        # 16 ms × 6° ≈ 0.96 s per turn, the same cadence as Weflow's
        # ``animation: spin 1s linear infinite``.
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = self.palette().color(QPalette.ColorRole.WindowText)
        colour.setAlpha(165)
        pen = QPen(colour)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Lucide Loader2 is a 270° arc with a gap, not a short 100° dash.
        painter.drawArc(self.rect().adjusted(2, 2, -2, -2), -self._angle * 16, 270 * 16)


__all__ = ["Spinner"]
