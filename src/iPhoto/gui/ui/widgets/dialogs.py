"""Reusable dialog helpers for the desktop UI.

Every message and confirmation in the application goes through this module so
they share one chrome (see :class:`~iPhoto.gui.ui.widgets.message_dialog.MessageDialog`)
instead of falling back to native ``QMessageBox`` windows that ignore the app's
styling. Only the directory picker stays native: file-system pickers are an OS
affordance users expect to look like the OS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QFileDialog, QWidget

from ....i18n import tr
from .message_dialog import MessageDialog


def select_directory(parent: QWidget, caption: str, start: Optional[Path] = None) -> Optional[Path]:
    """Return a directory selected by the user or ``None`` when cancelled."""

    directory = str(start) if start is not None else ""
    path = QFileDialog.getExistingDirectory(parent, caption, directory)
    if not path:
        return None
    return Path(path)


def show_error(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking error message."""

    MessageDialog(
        parent,
        title=title,
        message=message,
        buttons=[(tr("dialog.ok"), "ok")],
    ).exec()


def show_information(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking informational message."""

    MessageDialog(
        parent,
        title=title,
        message=message,
        buttons=[(tr("dialog.ok"), "ok")],
    ).exec()


def show_warning(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking warning message."""

    show_information(parent, message, title=title)


def confirm_action(
    parent: QWidget,
    message: str,
    *,
    title: str = "Confirmation",
    yes_label: str | None = None,
    no_label: str | None = None,
    danger: bool = False,
) -> bool:
    """Ask the user to confirm an action.

    Returns:
        True if the user selected the affirmative option, False otherwise.
    """

    yes_label = yes_label or tr("dialog.ok")
    no_label = no_label or tr("dialog.cancel")
    dialog = MessageDialog(
        parent,
        title=title,
        message=message,
        buttons=[(no_label, "no"), (yes_label, "yes")],
        primary_role="danger" if danger else "default",
    )
    dialog.exec()
    return dialog.result_key == "yes"


def ask_choice(
    parent: QWidget,
    message: str,
    *,
    title: str,
    choices: list[tuple[str, str]],
) -> str:
    """Show *choices* as buttons and return the chosen key (or ``"cancel"``)."""

    dialog = MessageDialog(parent, title=title, message=message, buttons=choices)
    dialog.exec()
    return dialog.result_key


__all__ = [
    "MessageDialog",
    "ask_choice",
    "confirm_action",
    "select_directory",
    "show_error",
    "show_information",
    "show_warning",
]
