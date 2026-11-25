"""Windows taskbar identity.

Kept free of Qt imports: both entry points call it before a QApplication (or the
native splash window) exists, and Windows captures the identity when the first
taskbar button is created.
"""

from __future__ import annotations

import sys

APP_USER_MODEL_ID = "iPhotron.iPhoto"
"""Must match the value written to the desktop shortcut (``System.AppUserModel.ID``)."""


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Declare this process' Windows taskbar identity.

    A process launched as ``python.exe run_iphoto.pyw`` is otherwise grouped
    under the interpreter, so the taskbar button keeps Python's document icon no
    matter which icon the window sets.
    """

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:  # noqa: BLE001 - cosmetic only, never block startup
        return False


__all__ = ["APP_USER_MODEL_ID", "set_app_user_model_id"]
