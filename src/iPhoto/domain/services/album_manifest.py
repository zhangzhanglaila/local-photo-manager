"""The album manifest: one schema, one default, one normalisation rule.

The same "read, repair, write back" routine was copied into five places
(application service, infrastructure repository, scan service, asset lifecycle
service, navigation coordinator). They agreed on the schema string and the id, but
one detail differed in a way that hurt: every copy forced ``title`` back to the
directory name, so a custom album name was silently overwritten on the next read.

This module owns that logic; callers read a manifest and, when it changed, write it
through their own IO layer.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ...errors import IPhotoError
from ...schemas import validate_album

#: Schema identifier stored in every album manifest.
ALBUM_MANIFEST_SCHEMA = "iPhoto/album@1"


def default_album_manifest(
    root: Path, *, album_id: str | None = None
) -> dict[str, Any]:
    """Return a fresh manifest for *root*."""

    return {
        "schema": ALBUM_MANIFEST_SCHEMA,
        "id": album_id or str(uuid.uuid4()),
        "title": root.name,
        "filters": {},
    }


def normalise_album_manifest(root: Path, manifest: dict[str, Any]) -> bool:
    """Bring *manifest* in line with the current schema; return whether it changed.

    A manifest whose ``title`` is a non-empty string keeps it — the directory name is
    only a fallback for missing or invalid titles. Structural problems that fail
    validation reset the manifest to defaults, because a corrupt file cannot be
    repaired piecemeal.
    """

    changed = False

    if manifest.get("schema") != ALBUM_MANIFEST_SCHEMA:
        manifest["schema"] = ALBUM_MANIFEST_SCHEMA
        changed = True

    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        manifest["title"] = root.name
        changed = True

    manifest_id = manifest.get("id")
    if not isinstance(manifest_id, str) or not manifest_id:
        manifest["id"] = str(uuid.uuid4())
        changed = True

    filters = manifest.get("filters")
    if not isinstance(filters, dict):
        manifest["filters"] = {}
        changed = True

    try:
        validate_album(manifest)
    except IPhotoError:
        manifest.clear()
        manifest.update(default_album_manifest(root))
        validate_album(manifest)
        changed = True

    return changed


__all__ = [
    "ALBUM_MANIFEST_SCHEMA",
    "default_album_manifest",
    "normalise_album_manifest",
]
