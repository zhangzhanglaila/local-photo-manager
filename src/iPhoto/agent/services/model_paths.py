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

``migrate_legacy_tree()`` only moves packs the canonical tree does not have yet:
when both copies exist it leaves the legacy one alone (the canonical copy may be
newer). Byte-identical leftovers are therefore never reclaimed automatically —
run ``scripts/cleanup_legacy_models.py`` (dry-run by default) to hash-check and
delete them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_LEGACY_WARNED = False


def _package_root() -> Path:
    """The `src` directory that contains `iPhoto`.

    The one place in the code base that knows how deep this package sits; every
    other module asks :func:install_root instead of doing its own arithmetic.
    """

    return Path(__file__).resolve().parents[3]


def install_root() -> Path:
    """Canonical model directory: the one shown to the user.

    `IPHOTO_MODELS_DIR` moves every pack at once (packaging, tests, users who
    keep models on another drive); without it the checkout/install layout decides.
    """

    override = os.environ.get("IPHOTO_MODELS_DIR")
    if override:
        return Path(override).expanduser()
    return _package_root().parent / "extension" / "models"


def legacy_roots() -> tuple[Path, ...]:
    """Older locations that are still read for compatibility."""

    # The tree that faces/OCR used before this module existed.
    return (_package_root() / "extension" / "models",)


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


def migrate_legacy_tree() -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Move packs from legacy roots into the canonical folder.

    Returns `(moved, skipped)`: *moved* pairs are `(source, destination)`;
    *skipped* entries already exist under the canonical root and are left in place
    (never overwritten — the canonical copy may be newer). Only same-volume moves
    are attempted, so startup can never stall on copying hundreds of megabytes.
    """

    target_root = install_root()
    moved: list[tuple[Path, Path]] = []
    skipped: list[Path] = []
    for legacy in legacy_roots():
        if not legacy.is_dir() or legacy.resolve() == target_root.resolve():
            continue
        try:
            if legacy.drive.lower() != target_root.drive.lower():
                _LOGGER.info(
                    "Keeping AI models in %s: a different drive than %s",
                    legacy,
                    target_root,
                )
                continue
        except OSError:  # pragma: no cover - drive introspection on odd mounts
            continue
        target_root.mkdir(parents=True, exist_ok=True)
        for entry in sorted(legacy.iterdir()):
            destination = target_root / entry.name
            if destination.exists():
                skipped.append(entry)
                continue
            try:
                entry.rename(destination)
            except OSError as exc:
                _LOGGER.warning("Could not move AI model %s: %s", entry, exc)
                continue
            moved.append((entry, destination))
            _LOGGER.info("Moved AI model %s -> %s", entry, destination)
    return moved, skipped


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    return _sha256(left) == _sha256(right)


def _same_tree(left: Path, right: Path) -> bool:
    """Return ``True`` when both paths contain the same files with the same bytes."""

    if left.is_file() or right.is_file():
        return left.is_file() and right.is_file() and _same_file(left, right)
    left_files = {item.relative_to(left): item for item in left.rglob("*") if item.is_file()}
    right_files = {item.relative_to(right): item for item in right.rglob("*") if item.is_file()}
    if left_files.keys() != right_files.keys():
        return False
    return all(_same_file(left_files[key], right_files[key]) for key in left_files)


def legacy_duplicate_report() -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    """Classify legacy entries that also exist under the canonical root.

    Returns ``(identical, differing)`` lists of ``(legacy, canonical)`` pairs.
    ``differing`` entries are duplicates by name only and must not be deleted
    automatically.
    """

    target_root = install_root()
    identical: list[tuple[Path, Path]] = []
    differing: list[tuple[Path, Path]] = []
    for legacy in legacy_roots():
        if not legacy.is_dir() or legacy.resolve() == target_root.resolve():
            continue
        for entry in sorted(legacy.iterdir()):
            if entry.is_dir() and not any(entry.rglob("*")):
                # An empty leftover directory is junk, not a duplicate; the
                # apply step prunes it.
                continue
            destination = target_root / entry.name
            if not destination.exists():
                continue
            if _same_tree(entry, destination):
                identical.append((entry, destination))
            else:
                differing.append((entry, destination))
    return identical, differing


def _prune_empty_dirs(root: Path) -> None:
    """Remove empty directories under *root* (bottom-up), then *root* itself."""

    if not root.is_dir():
        return
    directories = sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            continue
    try:
        root.rmdir()
    except OSError:
        pass


def remove_identical_legacy_duplicates(
    *, apply: bool = False
) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]], int]:
    """Reclaim legacy packs that are byte-identical to the canonical copy.

    ``apply=False`` only plans (no deletion). Returns ``(removed, kept, freed)``
    where *kept* are differing or unreadable duplicates and *freed* is the number
    of bytes the identical copies occupied.
    """

    identical, differing = legacy_duplicate_report()
    removed: list[tuple[Path, Path]] = []
    kept: list[tuple[Path, Path]] = list(differing)
    freed = 0
    for legacy, destination in identical:
        try:
            size = _tree_size(legacy)
            if apply:
                if legacy.is_dir():
                    shutil.rmtree(legacy)
                else:
                    legacy.unlink()
        except OSError as exc:  # pragma: no cover - locked files on Windows
            _LOGGER.warning("Could not remove legacy model %s: %s", legacy, exc)
            kept.append((legacy, destination))
            continue
        removed.append((legacy, destination))
        freed += size
    if apply:
        for legacy_root in legacy_roots():
            _prune_empty_dirs(legacy_root)
    return removed, kept, freed


__all__ = [
    "candidate_roots",
    "existing_dirs",
    "install_root",
    "legacy_duplicate_report",
    "legacy_roots",
    "migrate_legacy_tree",
    "remove_identical_legacy_duplicates",
    "resolve_dir",
]
