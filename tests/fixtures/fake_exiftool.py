"""Minimal ExifTool stand-in used by the session tests.

Supports both invocation styles the application uses:

* one-shot: ``exiftool -n -g1 -json -charset filename=utf8 -@ <argfile>``
* persistent: ``exiftool -stay_open True -@ -`` with ``-execute`` framing

Every start appends a line to ``FAKE_EXIFTOOL_LOG`` so tests can verify whether a
process was reused. Set ``FAKE_EXIFTOOL_EXIT_AFTER`` to make the persistent
process die after N requests (crash-recovery test).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _log_start() -> None:
    log_path = os.environ.get("FAKE_EXIFTOOL_LOG")
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("start\n")


def _payload(paths: list[str]) -> str:
    return json.dumps([{"SourceFile": path, "Fake": True} for path in paths])


def _run_one_shot(argv: list[str]) -> int:
    paths: list[str] = []
    if "-@" in argv:
        arg_file = Path(argv[argv.index("-@") + 1])
        paths = [
            line.strip()
            for line in arg_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        paths = [item for item in argv[1:] if not item.startswith("-")]
    sys.stdout.write(_payload(paths))
    return 0


def _run_stay_open() -> int:
    limit = int(os.environ.get("FAKE_EXIFTOOL_EXIT_AFTER", "0") or 0)
    handled = 0
    pending: list[str] = []
    expect_value = False
    closing = False
    for line in sys.stdin:
        token = line.rstrip("\n")
        if closing:
            # ``-stay_open\nFalse`` is the shutdown handshake.
            return 0
        if expect_value:
            expect_value = False
            continue
        if token in {"-charset", "-common_args"}:
            expect_value = True
            continue
        if token == "-stay_open":
            closing = True
            continue
        if token == "-execute":
            sys.stdout.write(_payload(pending))
            sys.stdout.write("\n{ready}\n")
            sys.stdout.flush()
            pending = []
            handled += 1
            if limit and handled >= limit:
                return 0
            continue
        if token.startswith("-") or not token.strip():
            continue
        pending.append(token)
    return 0


def main(argv: list[str]) -> int:
    _log_start()
    if "-stay_open" in argv:
        return _run_stay_open()
    return _run_one_shot(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
