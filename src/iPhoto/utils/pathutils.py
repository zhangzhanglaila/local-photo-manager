"""Utilities for working with filesystem paths inside iPhoto."""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Optional, Tuple

from ..config import ALL_WORK_DIR_NAMES, RECENTLY_DELETED_DIR_NAME, WORK_DIR_NAME

LOGGER = logging.getLogger(__name__)
_WARNED_WORK_DIR_CONFLICTS: set[Path] = set()
_CUSTOM_WORKSPACE_BASE: Optional[Path] = None


def set_custom_workspace_base(base: Path | None) -> None:
    """设置自定义工作空间的基础路径。

    Args:
        base: 自定义工作空间基础路径。如果为 None，则使用照片文件夹内的 .iPhoto 目录。
    """
    global _CUSTOM_WORKSPACE_BASE
    _CUSTOM_WORKSPACE_BASE = base


def get_custom_workspace_base() -> Optional[Path]:
    """获取当前设置的自定义工作空间基础路径。"""
    return _CUSTOM_WORKSPACE_BASE


def _generate_library_name(library_root: Path) -> str:
    """为照片库生成唯一的文件夹名称。

    优先使用文件夹名称，如果名称包含特殊字符则使用哈希值。
    """
    name = library_root.name
    # 检查是否包含可能引起问题的字符
    if re.match(r'^[\w一-鿿\-\s]+$', name, re.UNICODE):
        return name
    # 使用哈希值作为后备
    return hashlib.md5(str(library_root).encode()).hexdigest()[:16]


def get_custom_workspace_dir(library_root: Path) -> Path | None:
    """获取照片库的自定义工作目录。

    当设置了自定义工作空间基础路径时，返回该基础路径下的库专属目录。
    目录结构: <workspace_base>/<library_name>/.iPhoto/

    Args:
        library_root: 照片库的根目录。

    Returns:
        自定义工作目录路径，如果未设置自定义基础路径则返回 None。
    """
    base = get_custom_workspace_base()
    if base is None:
        return None

    library_name = _generate_library_name(library_root)
    workspace_dir = base / library_name / WORK_DIR_NAME
    return workspace_dir


def _expand(pattern: str) -> Iterator[str]:
    match = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if not match:
        yield pattern
        return
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    for option in match.group(1).split(","):
        yield from _expand(prefix + option + suffix)


@functools.lru_cache(maxsize=128)
def _expand_cached(pattern: str) -> Tuple[str, ...]:
    """Return a tuple of expanded patterns from *pattern* with caching.

    This wraps :func:`_expand` to allow caching of the expansion results,
    which is beneficial when the same patterns are checked against many files.
    """
    return tuple(_expand(pattern))


def is_excluded(path: Path, globs: Iterable[str], *, root: Path) -> bool:
    """Return ``True`` if *path* should be excluded based on *globs*.

    The function works on relative POSIX-style paths to provide consistent
    behaviour across operating systems.
    """

    rel = path.relative_to(root).as_posix()
    for pattern in globs:
        for expanded in _expand_cached(pattern):
            if fnmatch.fnmatch(rel, expanded):
                return True
            if expanded.startswith("**/") and fnmatch.fnmatch(rel, expanded[3:]):
                return True
    return False


def should_include(path: Path, include_globs: Iterable[str], exclude_globs: Iterable[str], *, root: Path) -> bool:
    """Return ``True`` if *path* should be scanned."""

    if is_excluded(path, exclude_globs, root=root):
        return False
    rel = path.relative_to(root).as_posix()
    for pattern in include_globs:
        for expanded in _expand_cached(pattern):
            if fnmatch.fnmatch(rel, expanded):
                return True
            if expanded.startswith("**/") and fnmatch.fnmatch(rel, expanded[3:]):
                return True
    return False


ALBUM_MANIFEST_NAME = ".iphoto.album.json"


def album_manifest_path(root: Path) -> Path:
    """Return where the album manifest for *root* must be written.

    The documented contract for a custom workspace is that the photo folder
    stays clean ("照片文件夹保持干净，只包含照片和视频"), but the manifest was always
    written as ``<library>/.iphoto.album.json`` no matter where the workspace
    lived, so a state file ended up next to the photos.

    With a workspace configured the manifest belongs in it, beside the index it
    describes. Without one it stays in the album folder, which keeps a
    self-contained album portable.
    """

    workspace = get_custom_workspace_dir(root)
    if workspace is not None:
        return workspace / "manifest.json"
    return root / ALBUM_MANIFEST_NAME


def find_album_manifest(root: Path) -> Path | None:
    """Return the manifest that belongs to *root*, migrating a legacy one.

    Albums created before custom workspaces keep their manifest in the album
    folder. When a workspace is configured that file is moved into it once, so
    the album keeps its identity and the photo folder ends up clean without the
    user having to do anything.
    """

    target = album_manifest_path(root)
    if target.exists():
        return target

    legacy_candidates = [root / ALBUM_MANIFEST_NAME, root / WORK_DIR_NAME / "manifest.json"]
    for candidate in legacy_candidates:
        if not candidate.exists():
            continue
        if target.parent == candidate.parent:
            return candidate
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate.replace(target)
        except OSError:
            LOGGER.warning(
                "Could not move album manifest %s into the workspace at %s",
                candidate,
                target,
            )
            return candidate
        LOGGER.info("Moved album manifest %s -> %s", candidate, target)
        return target
    return None


def _exact_work_dir_entries(root: Path) -> dict[str, Path]:
    try:
        return {
            entry.name: entry
            for entry in root.iterdir()
            if entry.name in ALL_WORK_DIR_NAMES and entry.is_dir()
        }
    except OSError:
        return {}


def resolve_work_dir(root: Path) -> Path | None:
    """Return the existing managed work directory for *root*, if any.

    优先返回自定义工作目录（如果配置），否则查找照片文件夹内的 .iPhoto 目录。
    ``.iPhoto`` is canonical, while lowercase ``.iphoto`` remains readable for
    legacy libraries on case-sensitive filesystems.
    """
    # 首先检查自定义工作目录
    custom_dir = get_custom_workspace_dir(root)
    if custom_dir is not None and custom_dir.exists():
        return custom_dir

    # 回退到照片文件夹内的传统工作目录
    entries = _exact_work_dir_entries(root)
    for name in ALL_WORK_DIR_NAMES:
        candidate = entries.get(name)
        if candidate is not None:
            return candidate
    return None


def ensure_work_dir(root: Path, name: str = WORK_DIR_NAME) -> Path:
    """Ensure that the album work directory exists and return it.

    优先使用自定义工作目录（如果配置），否则在照片文件夹内创建。
    New libraries use canonical ``.iPhoto``. If a legacy lowercase work
    directory already exists, continue using it instead of creating a case-only
    sibling that would split state on Linux.
    """

    # 首先检查是否应该使用自定义工作目录
    custom_dir = get_custom_workspace_dir(root)
    if custom_dir is not None:
        custom_dir.mkdir(parents=True, exist_ok=True)
        return custom_dir

    # 回退到照片文件夹内的传统工作目录
    if name == WORK_DIR_NAME:
        entries = _exact_work_dir_entries(root)
        canonical = entries.get(WORK_DIR_NAME) or (root / WORK_DIR_NAME)
        legacy_dirs = [
            entries[legacy_name]
            for legacy_name in ALL_WORK_DIR_NAMES[1:]
            if legacy_name in entries
        ]
        if WORK_DIR_NAME in entries:
            if legacy_dirs:
                resolved_root = root.resolve()
                if resolved_root not in _WARNED_WORK_DIR_CONFLICTS:
                    _WARNED_WORK_DIR_CONFLICTS.add(resolved_root)
                    LOGGER.warning(
                        "Both canonical %s and legacy work directories exist under %s; using %s",
                        WORK_DIR_NAME,
                        root,
                        canonical,
                    )
            return canonical
        if legacy_dirs:
            return legacy_dirs[0]

    work_dir = root / name
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def normalise_for_compare(path: Path) -> Path:
    """Return a normalised ``Path`` suitable for cross-platform comparisons.

    ``Path.resolve`` is insufficient on its own because it preserves the
    original casing on case-insensitive filesystems.  Combining
    :func:`os.path.realpath` with :func:`os.path.normcase` yields a canonical
    representation that collapses symbolic links and performs the necessary
    case folding so that two references to the same directory compare equal
    regardless of how they were produced.
    """

    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = str(path)
    return Path(os.path.normcase(resolved))


def is_descendant_path(path: Path, candidate_root: Path) -> bool:
    """Return ``True`` when *path* is located under *candidate_root*.

    The helper treats equality as a positive match so callers can avoid
    special casing.  ``Path.parents`` yields every ancestor of *path*, making
    it a convenient way to check the relationship without manual string
    operations that could break across platforms.
    """

    if path == candidate_root:
        return True

    return candidate_root in path.parents


def normalise_rel_value(value: object) -> Optional[str]:
    """Return a POSIX-formatted relative path for *value* when possible.

    Raises:
        TypeError: If *value* is truthy but not a str or Path.
    """

    if not value:
        return None

    if isinstance(value, (str, Path)):
        return Path(str(value)).as_posix()

    raise TypeError(f"Expected str or Path, got {type(value).__name__}")


def is_trash_rel(rel: object) -> bool:
    """Return whether a library-relative *rel* belongs to the trash album."""

    if not isinstance(rel, (str, Path)) or not rel:
        return False
    parts = PurePosixPath(str(rel).replace("\\", "/")).parts
    return bool(parts) and parts[0] == RECENTLY_DELETED_DIR_NAME


def resolve_trash_dir(root: Path) -> Optional[Path]:
    """Return the trash directory for *root* without creating anything.

    The trash normally lives inside the library's work directory, which can be
    relocated to a custom workspace base and therefore sit outside the library
    itself. Older libraries may still have a root-level ``.Trash``.
    """

    root = Path(root)
    work_dir = resolve_work_dir(root)
    if work_dir is not None:
        candidate = work_dir / RECENTLY_DELETED_DIR_NAME
        if candidate.is_dir():
            return candidate
    legacy = root / RECENTLY_DELETED_DIR_NAME
    if legacy.is_dir():
        return legacy
    if work_dir is not None:
        return work_dir / RECENTLY_DELETED_DIR_NAME
    return None


def resolve_asset_path(root: Path, rel: object) -> Path:
    """Resolve an index ``rel`` value to the path the file actually occupies.

    Trashed assets are indexed under the canonical ``.Trash/`` prefix, but the
    trash directory is not necessarily inside the library root, so resolving
    them against the library root would point at a file that does not exist.
    Absolute ``rel`` values (used for roots outside the library) pass through.
    """

    path = Path(str(rel))
    if path.is_absolute():
        return path
    if is_trash_rel(path):
        trash_dir = resolve_trash_dir(root)
        if trash_dir is not None:
            parts = PurePosixPath(path.as_posix()).parts
            return trash_dir.joinpath(*parts[1:])
    return Path(root) / path
