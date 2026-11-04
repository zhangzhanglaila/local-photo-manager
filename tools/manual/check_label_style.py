"""Manually check whether a child ``QLabel`` inherits a parent's border rule.

This is a developer smoke script, not a pytest test. Run it by hand:

    .venv\\Scripts\\python.exe tools\\manual\\check_label_style.py

Expected: the labels render without red borders, because the border is declared
on the parent widget only. It lives under ``tools/manual`` so a bare ``pytest``
run does not collect it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def main() -> int:
    app = QApplication.instance() or QApplication([])
    _ = app

    # 测试 QLabel 是否继承父容器的边框样式
    widget = QWidget()
    widget.setStyleSheet("border: 2px solid red;")
    layout = QVBoxLayout(widget)

    label1 = QLabel("第一行")
    label2 = QLabel("第二行")
    layout.addWidget(label1)
    layout.addWidget(label2)

    widget.show()
    print(
        "Check if labels have borders - they should NOT if they don't have their "
        "own stylesheet"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
