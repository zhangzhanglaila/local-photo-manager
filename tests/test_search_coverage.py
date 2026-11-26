"""Coverage numbers shown in the settings page."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from iPhoto.agent.services import search_coverage


def _make_db(path: Path, *, photos: int, embeddings: dict[str, int]) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE assets (id TEXT PRIMARY KEY, media_type INTEGER,"
            " live_role INTEGER)"
        )
        connection.executemany(
            "INSERT INTO assets VALUES (?, ?, ?)",
            [(f"p{i}", 0, 0) for i in range(photos)]
            + [(f"v{i}", 1, 0) for i in range(3)],
        )
        connection.execute(
            "CREATE TABLE embeddings (asset_id TEXT, model_name TEXT,"
            " embedding BLOB, dimension INTEGER,"
            " PRIMARY KEY (asset_id, model_name))"
        )
        rows = []
        for model, count in embeddings.items():
            rows.extend((f"p{i}", model, b"", 768) for i in range(count))
        connection.executemany("INSERT INTO embeddings VALUES (?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()


def test_coverage_counts_photos_and_one_model(tmp_path, monkeypatch):
    db = tmp_path / "global_index.db"
    _make_db(db, photos=5, embeddings={"siglip2-base-patch16-224": 3, "clip-vit-base-patch32": 5})

    monkeypatch.setattr(search_coverage, "_db_path", lambda root: db)
    monkeypatch.setattr(search_coverage, "count_indexed", lambda root, model: 3)

    assert search_coverage.count_photos(tmp_path) == 5
    assert search_coverage.compute_coverage(tmp_path, "siglip2-base-patch16-224") == (3, 5)


def test_missing_database_reports_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(search_coverage, "_db_path", lambda root: None)

    assert search_coverage.count_photos(tmp_path) == 0
