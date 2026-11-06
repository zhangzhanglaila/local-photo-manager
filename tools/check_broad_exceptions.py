#!/usr/bin/env python3
"""Keep the count of broad exception handlers from growing.

``except Exception`` is sometimes correct (per-file isolation in a scanner, optional
third-party imports) but it is also the easiest way to turn a real bug into silence.
Ruff already selects ``BLE``; there is no CI, so this ratchet runs as part of the
local architecture check: the number may go down, never up.

Run with ``--update`` to accept the current count after deliberately removing or
adding handlers.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Handlers allowed today. Lower this whenever handlers are narrowed or removed.
BROAD_EXCEPTION_BUDGET = 425


def _source_files(src_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in (src_root, src_root.parent / "maps"):
        if root.is_dir():
            files.extend(path for path in root.rglob("*.py") if path.is_file())
    return files


def count_broad_exception_handlers(src_root: Path) -> int:
    """Return how many ``except Exception`` / ``except BaseException`` exist."""

    total = 0
    for path in _source_files(src_root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        total += text.count("except Exception")
        total += text.count("except BaseException")
    return total


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    src_root = Path(__file__).resolve().parent.parent / "src" / "iPhoto"
    count = count_broad_exception_handlers(src_root)

    if "--update" in argv:
        print(f"Broad exception budget accepted at {count}.")
        print("Set BROAD_EXCEPTION_BUDGET in tools/check_broad_exceptions.py to this value.")
        return 0

    if count > BROAD_EXCEPTION_BUDGET:
        print(
            f"ERROR: {count} broad exception handlers, budget is "
            f"{BROAD_EXCEPTION_BUDGET}. Narrow the new handlers or justify them "
            "with a `# noqa: BLE001` comment."
        )
        return 1

    print(f"OK - {count} broad exception handlers (budget {BROAD_EXCEPTION_BUDGET}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
