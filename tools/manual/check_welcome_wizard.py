"""Manually exercise the first-run welcome wizard.

This is a developer smoke script, not a pytest test: it builds a real
``QApplication`` and may open a dialog. Run it by hand from the repository root:

    .venv\\Scripts\\python.exe tools\\manual\\check_welcome_wizard.py

It lives under ``tools/manual`` so a bare ``pytest`` run does not collect it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `src/` importable no matter which directory the script is started from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # 使用离屏模式


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from iPhoto.bootstrap.runtime_context import RuntimeContext
    from iPhoto.utils.pathutils import set_custom_workspace_base

    print("Testing welcome wizard...")

    # 创建应用实例
    app = QApplication.instance() or QApplication([])
    _ = app

    # 创建运行时上下文
    context = RuntimeContext.create(defer_startup=True)

    # 清除自定义工作目录设置以模拟需要配置的情况
    set_custom_workspace_base(None)

    # 测试 needs_workspace_config
    needs_config = context.needs_workspace_config()
    print(f"needs_workspace_config() = {needs_config}")

    if needs_config:
        print("Showing welcome wizard...")
        result = context.show_welcome_wizard()
        print(f"User selected: {result}")
    else:
        print("No workspace config needed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - manual script reports and continues
        print(f"Error: {exc}")
        import traceback

        traceback.print_exc()
        raise SystemExit(1) from exc
