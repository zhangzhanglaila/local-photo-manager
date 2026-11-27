#!/usr/bin/env python3
"""Reclaim disk space from the legacy ``src/extension/models`` tree.

``model_paths.migrate_legacy_tree()`` never deletes a pack that already exists
under the canonical ``extension/models`` root (the canonical copy may be newer),
so byte-identical leftovers stay on disk forever. This tool hash-checks them and
removes only the copies that are provably identical.

Dry-run by default::

    python scripts/cleanup_legacy_models.py

Delete the identical copies::

    python scripts/cleanup_legacy_models.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from iPhoto.agent.services import model_paths  # noqa: E402


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete the byte-identical legacy copies (default: dry run)",
    )
    args = parser.parse_args(argv)

    canonical = model_paths.install_root()
    legacy = model_paths.legacy_roots()
    print(f"canonical: {canonical}")
    for root in legacy:
        print(f"legacy:    {root}")

    removed, kept, freed = model_paths.remove_identical_legacy_duplicates(apply=args.apply)

    for source, target in removed:
        action = "removed" if args.apply else "would remove"
        print(f"  {action}: {source} (identical to {target})")
    for source, target in kept:
        print(f"  kept:     {source} (differs from {target}; inspect manually)")

    verb = "freed" if args.apply else "reclaimable"
    print(f"{verb}: {_human_size(freed)}")
    if removed and not args.apply:
        print("re-run with --apply to delete the identical copies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
