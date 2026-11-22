"""Settings action: relocate the library workspace to another folder.

The user picks a destination, the workspace is copied with a progress dialog, verified,
and only then is the old directory removed and ``workspace_base`` updated. The change
needs a restart because the databases are already open by the running process.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from ....bootstrap.workspace_relocation import RelocationError, relocate_workspace
from ....i18n import tr
from ....utils.pathutils import get_custom_workspace_base, get_custom_workspace_dir
from ..widgets import dialogs

_LOGGER = logging.getLogger(__name__)


def _current_workspace(library_root: Path | None) -> Path | None:
    if library_root is None:
        return None
    return get_custom_workspace_dir(Path(library_root))


def relocate_workspace_interactively(
    parent: QWidget,
    library_root: Path | None,
    settings,
) -> bool:
    """Ask for a destination, copy the workspace there, and switch the setting.

    Returns True when the relocation succeeded (a restart is then required).
    """

    current = _current_workspace(library_root)
    if current is None or not current.is_dir():
        QMessageBox.warning(
            parent,
            tr("dialog.workspace_relocate_title"),
            tr("dialog.workspace_relocate_no_workspace"),
        )
        return False

    # Show where the data lives right now *before* asking for a new folder: the picker
    # alone gave no context, so it was impossible to tell what was being changed.
    preview = QMessageBox(parent)
    preview.setIcon(QMessageBox.Icon.Information)
    preview.setWindowTitle(tr("dialog.workspace_relocate_title"))
    preview.setText(tr("dialog.workspace_current_location", path=str(current)))
    preview.setInformativeText(tr("dialog.workspace_change_prompt"))
    change_button = preview.addButton(
        tr("action.change_workspace"), QMessageBox.ButtonRole.AcceptRole
    )
    preview.addButton(QMessageBox.StandardButton.Cancel)
    preview.exec()
    if preview.clickedButton() is not change_button:
        return False

    start = get_custom_workspace_base() or current.parent
    chosen = dialogs.select_directory(
        parent, tr("dialog.select_workspace_location"), start=Path(start)
    )
    if chosen is None:
        return False

    library_name = current.name
    destination = Path(chosen) / library_name
    if destination == current:
        QMessageBox.information(
            parent,
            tr("dialog.workspace_relocate_title"),
            tr("dialog.workspace_relocate_same"),
        )
        return False

    confirmed = QMessageBox.question(
        parent,
        tr("dialog.workspace_relocate_title"),
        tr("dialog.workspace_relocate_confirm", destination=str(destination)),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if confirmed != QMessageBox.StandardButton.Yes:
        return False

    progress = QProgressDialog(
        tr("dialog.workspace_relocate_copying"), "", 0, 100, parent
    )
    progress.setWindowTitle(tr("dialog.workspace_relocate_title"))
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    def on_progress(done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        progress.setValue(min(100, percent))
        QApplication.processEvents()

    try:
        result = relocate_workspace(
            current, destination, overwrite=False, on_progress=on_progress
        )
    except RelocationError as exc:
        progress.close()
        QMessageBox.critical(
            parent,
            tr("dialog.workspace_relocate_title"),
            tr("dialog.workspace_relocate_failed", error=str(exc)),
        )
        return False
    finally:
        progress.setValue(100)
        progress.close()

    # Verified copy succeeded; the old directory is only removed now.
    try:
        shutil.rmtree(current)
    except OSError:
        _LOGGER.warning("could not remove the previous workspace %s", current, exc_info=True)

    try:
        settings.set("workspace_base", str(Path(chosen)))
    except Exception:
        _LOGGER.warning("could not persist the new workspace base", exc_info=True)

    QMessageBox.information(
        parent,
        tr("dialog.workspace_relocate_title"),
        tr(
            "dialog.workspace_relocate_done",
            destination=str(result.destination),
        ),
    )
    return True
