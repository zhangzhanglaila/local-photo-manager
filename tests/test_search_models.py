"""Search model catalogue and per-model vector storage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iPhoto.agent.services import search_models
from iPhoto.utils import pathutils


@pytest.fixture(autouse=True)
def _restore_active_model():
    yield
    search_models.set_active_model(search_models.DEFAULT_SEARCH_MODEL_KEY)


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


def test_active_model_follows_the_setting(tmp_path: Path):
    spec = search_models.set_active_model("siglip2-base")
    assert spec.family == "siglip"
    assert search_models.active_model_key() == "siglip2-base"
    assert search_models.active_model().dimension == 768

    search_models.set_active_model("unknown-key")
    assert search_models.active_model_key() == search_models.DEFAULT_SEARCH_MODEL_KEY


def test_installed_detection_uses_the_model_directory(tmp_path: Path):
    spec = search_models.resolve_spec("siglip2-base")
    install_root = tmp_path / "install"

    assert search_models.is_installed(spec, install_root=install_root) is False

    target = search_models.model_dir(spec, install_root=install_root)
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")

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
