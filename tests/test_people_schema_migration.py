"""An incompatible People schema must be archived, never dropped.

``_drop_legacy_manual_faces`` ran ``DROP TABLE manual_faces`` whenever the stored
columns looked older than expected. That silently destroyed every manual face
annotation a user had made — irreversibly, and without a backup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from iPhoto.people.state_repository import PEOPLE_SCHEMA_VERSION, FaceStateRepository


def _legacy_database(path: Path) -> None:
    """Create a manual_faces table with the old column set plus one row."""

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE manual_faces ("
            "face_key TEXT PRIMARY KEY, asset_id TEXT, confidence REAL, "
            "embedding BLOB, detected_at TEXT)"
        )
        conn.execute(
            "INSERT INTO manual_faces VALUES ('face-1', 'asset-1', 0.9, x'00', '2024-01-01')"
        )
        conn.commit()
    finally:
        conn.close()


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def test_legacy_table_is_preserved_as_a_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "faces.db"
    _legacy_database(db_path)

    FaceStateRepository(db_path).initialize()

    tables = _tables(db_path)
    backups = [name for name in tables if name.startswith("manual_faces_legacy_")]
    assert backups, "the old table must be preserved, not dropped"
    assert "manual_faces" in tables, "a fresh table is created afterwards"

    conn = sqlite3.connect(db_path)
    try:
        preserved = conn.execute(f'SELECT COUNT(*) FROM "{backups[0]}"').fetchone()[0]
    finally:
        conn.close()
    assert preserved == 1, "the user's annotations survive the migration"


def test_schema_version_is_stamped(tmp_path: Path) -> None:
    db_path = tmp_path / "faces.db"

    FaceStateRepository(db_path).initialize()

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == PEOPLE_SCHEMA_VERSION


def test_current_schema_is_not_migrated_again(tmp_path: Path) -> None:
    db_path = tmp_path / "faces.db"
    repository = FaceStateRepository(db_path)
    repository.initialize()

    repository.initialize()

    tables = _tables(db_path)
    assert not [name for name in tables if name.startswith("manual_faces_legacy_")]


@pytest.mark.parametrize("version", [0, 1])
def test_initialize_is_idempotent_across_versions(tmp_path: Path, version: int) -> None:
    db_path = tmp_path / "faces.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()

    FaceStateRepository(db_path).initialize()
    FaceStateRepository(db_path).initialize()

    assert "manual_faces" in _tables(db_path)
