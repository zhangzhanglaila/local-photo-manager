"""Where the AI model weights live — one answer for every registry.

Three registries (search / faces / OCR) each had their own `models_root()` and
they disagreed: search resolved ``<repo>/extension/models`` while faces and OCR
resolved ``<repo>/src/extension/models``. Both trees existed on the same machine,
so a pack placed in the documented folder was reported as "not installed".

This module is the only place that answers the question. The canonical root is
``<repo>/extension/models`` (the folder the UI documents); the older
``<repo>/src/extension/models`` is still *read* so existing installs keep working
without re-downloading, and it is reported once per process so the stale tree can
be migrated later.
"""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_LEGACY_WARNED = False


def install_root() -> Path:
    """Canonical model directory: the one shown to the user."""

    # src/iPhoto/agent/services/model_paths.py -> repo root at parents[4]
    return Path(__file__).resolve().parents[4] / "extension" / "models"


def legacy_roots() -> tuple[Path, ...]:
    """Older locations that are still read for compatibility."""

    # The tree that faces/OCR used before this module existed.
    return (Path(__file__).resolve().parents[3] / "extension" / "models",)


def candidate_roots() -> tuple[Path, ...]:
    """Canonical first, then legacy; duplicates removed."""

    roots: list[Path] = [install_root()]
    for root in legacy_roots():
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _has_marker(directory: Path, patterns: tuple[str, ...]) -> bool:
    if not directory.is_dir():
        return False
    return any(any(directory.glob(pattern)) for pattern in patterns)


def resolve_dir(name: str, *, patterns: tuple[str, ...] = ("config.json",)) -> Path:
    """Return the directory holding pack *name*, preferring an existing copy.

    Falls back to ``install_root()/name`` so a first download lands in the
    documented location.
    """

    global _LEGACY_WARNED

    roots = candidate_roots()
    for index, root in enumerate(roots):
        directory = root / name
        if _has_marker(directory, patterns):
            if index > 0 and not _LEGACY_WARNED:
                _LEGACY_WARNED = True
                _LOGGER.warning(
                    "AI models are being read from the legacy location %s; "
                    "the canonical folder is %s",
                    root,
                    roots[0],
                )
            return directory
    return roots[0] / name


def existing_dirs(pattern: str) -> tuple[Path, ...]:
    """Return every directory matching *pattern* across the candidate roots."""

    found: list[Path] = []
    for root in candidate_roots():
        if not root.is_dir():
            continue
        for directory in sorted(root.glob(pattern)):
            if directory.is_dir() and directory not in found:
                found.append(directory)
    return tuple(found)


__all__ = [
    "candidate_roots",
    "existing_dirs",
    "install_root",
    "legacy_roots",
    "resolve_dir",
]
