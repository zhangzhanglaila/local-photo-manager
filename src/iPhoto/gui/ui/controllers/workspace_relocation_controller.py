"""Settings action: relocate the library workspace to another folder.

The user picks a destination, the workspace is copied with a progress dialog, verified,
and only then is the old directory removed and ``workspace_base`` updated. The change
needs a restart because the databases are already open by the running process.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

from ....bootstrap.workspace_relocation import RelocationError, relocate_workspace
from ....i18n import tr
from ....utils.pathutils import get_custom_workspace_base, get_custom_workspace_dir
from ..widgets import dialogs
from ..widgets.progress_dialog import ProgressDialog

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

    # The storage location is the workspace *base* (e.g. D:\iPhotronWorkspace): every
    # library lives in its own subfolder underneath, so migrating one subfolder would be
    # wrong. Migrate the whole tree.
    library_workspace = _current_workspace(library_root)
    current = get_custom_workspace_base() or (
        library_workspace.parent if library_workspace is not None else None
    )
    if current is None or not Path(current).is_dir():
        dialogs.show_warning(
            parent,
            tr("dialog.workspace_relocate_no_workspace"),
            title=tr("dialog.workspace_relocate_title"),
        )
        return False

    # Show where the data lives right now *before* asking for a new folder: the picker
    # alone gave no context, so it was impossible to tell what was being changed.
    choice = dialogs.ask_choice(
        parent,
        "\n\n".join(
            (
                tr("dialog.workspace_current_location", path=str(current)),
                tr("dialog.workspace_change_prompt"),
            )
        ),
        title=tr("dialog.workspace_relocate_title"),
        choices=[("取消", "cancel"), (tr("action.change_workspace"), "change")],
    )
    if choice != "change":
        return False

    start = get_custom_workspace_base() or current.parent
    chosen = dialogs.select_directory(
        parent, tr("dialog.select_workspace_location"), start=Path(start)
    )
    if chosen is None:
        return False

    destination = Path(chosen)
    if destination == Path(current):
        dialogs.show_information(
            parent,
            tr("dialog.workspace_relocate_same"),
            title=tr("dialog.workspace_relocate_title"),
        )
        return False

    confirmed = dialogs.confirm_action(
        parent,
        tr("dialog.workspace_relocate_confirm", destination=str(destination)),
        title=tr("dialog.workspace_relocate_title"),
        yes_label="迁移",
        no_label="取消",
    )
    if not confirmed:
        return False

    progress = ProgressDialog(
        parent,
        title=tr("dialog.workspace_relocate_title"),
        message=tr("dialog.workspace_relocate_copying"),
    )
    progress.set_progress(0, 100)
    progress.show()

    def on_progress(done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        progress.set_progress(min(100, percent), 100)
        QApplication.processEvents()

    try:
        result = relocate_workspace(
            Path(current), destination, overwrite=True, on_progress=on_progress
        )
    except RelocationError as exc:
        progress.close()
        dialogs.show_error(
            parent,
            tr("dialog.workspace_relocate_failed", error=str(exc)),
            title=tr("dialog.workspace_relocate_title"),
        )
        return False
    finally:
        progress.set_progress(100, 100)
        progress.close()

    # Verified copy succeeded; the old directory is only removed now.
    try:
        shutil.rmtree(Path(current))
    except OSError:
        _LOGGER.warning("could not remove the previous workspace %s", current, exc_info=True)

    try:
        settings.set("workspace_base", str(Path(chosen)))
    except Exception:
        _LOGGER.warning("could not persist the new workspace base", exc_info=True)

    dialogs.show_information(
        parent,
        tr(
            "dialog.workspace_relocate_done",
            destination=str(result.destination),
        ),
        title=tr("dialog.workspace_relocate_title"),
    )
    return True
