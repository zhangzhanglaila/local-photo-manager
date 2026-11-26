"""Reusable status bar tailored for the frameless main window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...services.background_tasks import BackgroundTaskRegistry


class _HiddenProgressBar(QProgressBar):
    """Progress bar that never becomes visible.

    ``StatusBarController`` still drives a bar for scan/load/import progress. The
    status bar now shows that progress through the background-task entry, so the
    controller's bar must stay invisible no matter what it asks for.
    """

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt-style API
        super().setVisible(False)


class ChromeStatusBar(QWidget):
    """Lightweight status bar with an opaque background and progress indicator.

    The widget emulates the small subset of :class:`QStatusBar` behaviour that the
    controllers rely on (``showMessage``/``clearMessage``) while guaranteeing that the
    background remains fully opaque inside the rounded window shell. Implementing a
    bespoke control avoids the transparency artefacts introduced by the native status bar
    when the main window uses ``Qt.WA_TranslucentBackground`` for anti-aliased corners.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        registry: "BackgroundTaskRegistry",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chromeStatusBar")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)

        # Keep the status bar opaque even though the parent window uses
        # ``WA_TranslucentBackground``. Copying the base colour into the Window
        # role ensures every style fills the background without inheriting
        # transparency from the palette.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, palette.color(QPalette.ColorRole.Base))
        self.setPalette(palette)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        self._message_label = QLabel(self)
        self._message_label.setObjectName("statusMessageLabel")
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._message_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self._message_label, 1)

        # Background-task entry sits at the right end, next to the window grip,
        # the way an IDE keeps its progress widget on the right.
        from .task_status_widget import TaskStatusButton

        self._task_button = TaskStatusButton(self, registry=registry)
        layout.addWidget(
            self._task_button, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # The old right-hand bar duplicated the task entry's progress; it is kept
        # as a hidden compatibility surface for controllers that still drive it.
        self._progress_bar = self._create_progress_bar()

        # Reserve horizontal space so the floating resize indicator and size grip can occupy
        # the bottom-right corner without overlapping the progress bar. A fixed-width spacer
        # keeps the layout intent explicit and makes future visual adjustments straightforward.
        resize_overlay_width = 25
        layout.addSpacerItem(
            QSpacerItem(
                resize_overlay_width,
                1,
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Minimum,
            )
        )

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self.clearMessage)

    def _create_progress_bar(self) -> QProgressBar:
        """Hidden compatibility surface; progress is drawn by the task entry."""

        bar = _HiddenProgressBar(self)
        bar.setObjectName("statusProgress")
        bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return bar

    @property
    def task_button(self):
        """Expose the background-task entry (tests and controllers)."""

        return self._task_button

    @property
    def progress_bar(self) -> QProgressBar:
        """Expose the embedded progress bar for controllers that drive it."""

        return self._progress_bar

    def showMessage(self, message: str, timeout: int = 0) -> None:  # noqa: N802 - Qt-style API
        """Display a status message optionally cleared after ``timeout`` milliseconds."""

        self._message_label.setText(message)
        self._clear_timer.stop()
        if timeout > 0:
            self._clear_timer.start(max(0, timeout))

    def clearMessage(self) -> None:  # noqa: N802 - Qt-style API
        """Remove the current message and cancel any pending timeout."""

        self._message_label.clear()
        self._clear_timer.stop()

    def currentMessage(self) -> str:  # noqa: N802 - Qt-style API
        """Return the text currently shown in the status bar."""

        return self._message_label.text()


__all__ = ["ChromeStatusBar"]
