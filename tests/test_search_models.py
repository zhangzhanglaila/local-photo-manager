"""Search model catalogue and per-model vector storage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iPhoto.agent.services import search_models
from iPhoto.utils import pathutils


def test_defaults_when_nothing_is_configured():
    assert search_models.resolve_spec(None).key == search_models.DEFAULT_SEARCH_MODEL_KEY
    assert search_models.resolve_spec("nope").key == search_models.DEFAULT_SEARCH_MODEL_KEY


def test_every_model_has_a_complete_spec():
    for spec in search_models.available_models():
        assert spec.download_mb > 0
        assert spec.dimension in (512, 768)
        assert spec.family in {"clip", "siglip", "chinese_clip"}
        assert spec.languages in {"en", "zh", "multilingual"}
        assert 1 <= spec.quality <= 3


def test_models_are_offered_cheapest_first():
    sizes = [spec.download_mb for spec in search_models.available_models()]
    assert sizes == sorted(sizes)


def test_active_model_follows_the_selection() -> None:
    from iPhoto.agent.services.model_selection import ModelSelection

    selection = ModelSelection()
    spec = selection.apply("search", "siglip2-base")
    assert spec.family == "siglip"
    assert selection.key("search") == "siglip2-base"
    assert selection.spec("search").dimension == 768

    selection.apply("search", "unknown-key")
    assert selection.key("search") == search_models.DEFAULT_SEARCH_MODEL_KEY


def test_installed_detection_uses_the_model_directory(tmp_path: Path):
    spec = search_models.resolve_spec("siglip2-base")
    install_root = tmp_path / "install"

    assert search_models.is_installed(spec, install_root=install_root) is False

    target = search_models.model_dir(spec, install_root=install_root)
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "model.safetensors").write_bytes(b"")

    assert search_models.is_installed(spec, install_root=install_root) is True
    assert spec in search_models.installed_models(install_root=install_root)
    assert search_models.available_models()[0] not in search_models.installed_models(
        install_root=install_root
    )


def test_embeddings_are_filtered_by_model(tmp_path: Path, monkeypatch):
    """Vectors from one model must never be compared with another model's."""

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", tmp_path / "workspace")
    library = tmp_path / "Library"
    library.mkdir()

    from iPhoto.cache.index_store import embedding_repository as module

    monkeypatch.setattr(module, "_global_instance", None)
    repo = module.EmbeddingRepository(library)
    vector = np.ones(4, dtype=np.float32) / 2.0
    repo.store_embeddings_batch(
        [
            ("as_old", vector, "clip-vit-base-patch32"),
            ("as_new", vector, "siglip2-base-patch16-224"),
        ]
    )

    old = repo.get_all_embeddings(model_name="clip-vit-base-patch32")
    new = repo.get_all_embeddings(model_name="siglip2-base-patch16-224")
    everything = repo.get_all_embeddings()

    assert {row["asset_id"] for row in old} == {"as_old"}
    assert {row["asset_id"] for row in new} == {"as_new"}
    assert len(everything) == 2

    pending = repo.get_asset_ids_without_embeddings(
        ["as_old", "as_new", "as_missing"], model_name="clip-vit-base-patch32"
    )
    assert pending == ["as_new", "as_missing"]
    repo.close()


def test_two_models_can_share_an_asset(tmp_path: Path, monkeypatch):
    """One row per (asset, model): switching models must not overwrite vectors."""

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", tmp_path / "workspace")
    library = tmp_path / "Library"
    library.mkdir()

    from iPhoto.cache.index_store import embedding_repository as module

    monkeypatch.setattr(module, "_global_instance", None)
    repo = module.EmbeddingRepository(library)
    vector = np.ones(4, dtype=np.float32) / 2.0
    repo.store_embeddings_batch([("as_one", vector, "clip-vit-base-patch32")])
    repo.store_embeddings_batch([("as_one", vector, "siglip2-base-patch16-224")])

    assert len(repo.get_all_embeddings()) == 2
    assert {row["model_name"] for row in repo.get_all_embeddings()} == {
        "clip-vit-base-patch32",
        "siglip2-base-patch16-224",
    }
    # Re-storing the same pair updates in place instead of duplicating.
    repo.store_embeddings_batch([("as_one", vector, "siglip2-base-patch16-224")])
    assert len(repo.get_all_embeddings()) == 2
    repo.close()


def test_old_single_key_database_is_migrated(tmp_path: Path, monkeypatch):
    """Libraries created before the model switch keep their vectors."""

    import sqlite3

    monkeypatch.setattr(pathutils, "_CUSTOM_WORKSPACE_BASE", tmp_path / "workspace")
    library = tmp_path / "Library"
    library.mkdir()
    work = pathutils.ensure_work_dir(library)
    work.mkdir(parents=True, exist_ok=True)
    db_path = work / "embeddings.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE embeddings (
            asset_id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            dimension INTEGER NOT NULL,
            model_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    legacy.execute(
        "INSERT INTO embeddings (asset_id, embedding, dimension, model_name) VALUES (?,?,?,?)",
        ("as_legacy", np.ones(4, dtype=np.float32).tobytes(), 4, None),
    )
    legacy.commit()
    legacy.close()

    from iPhoto.cache.index_store import embedding_repository as module

    monkeypatch.setattr(module, "_global_instance", None)
    repo = module.EmbeddingRepository(library)

    rows = repo.get_all_embeddings()
    assert [row["asset_id"] for row in rows] == ["as_legacy"]
    assert rows[0]["model_name"] == "clip-vit-base-patch32"

    # New model vectors can now coexist with the migrated one.
    repo.store_embeddings_batch(
        [("as_legacy", np.ones(4, dtype=np.float32), "siglip2-base-patch16-224")]
    )
    assert len(repo.get_all_embeddings()) == 2
    repo.close()
