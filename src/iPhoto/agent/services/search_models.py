"""Catalogue of the semantic search models the app can use.

The model is a user-facing choice: the first-run wizard asks for one, the
settings window can switch it later, and both write the same key so every code
path (encoder, indexer, search) agrees on which vectors are current.

Vectors are stored with the model that produced them (``embeddings.model_name``),
so switching models never mixes incompatible vector spaces: search only compares
vectors from the active model, and the indexer fills in the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Family = Literal["clip", "siglip", "chinese_clip"]

DEFAULT_SEARCH_MODEL_KEY = "clip-b32"

#: Settings key shared by the wizard, the settings window and the encoder.
SETTINGS_KEY = "search.model"

#: Writing a fresh value here asks the app to re-embed every asset with the
#: active model (settings UI has no direct handle on the running coordinator).
REBUILD_KEY = "search.rebuild_request"


@dataclass(frozen=True)
class SearchModelSpec:
    """Everything the UI and the encoder need to know about a model."""

    key: str
    dir_name: str
    hf_id: str
    modelscope_id: str | None
    family: Family
    dimension: int
    download_mb: int
    languages: str  # "en" | "zh" | "multilingual"
    quality: int  # 1 (basic) .. 3 (best)
    speed: str  # "fastest" | "fast" | "medium"

    @property
    def is_multilingual(self) -> bool:
        return self.languages == "multilingual"


SEARCH_MODELS: dict[str, SearchModelSpec] = {
    "clip-b32": SearchModelSpec(
        key="clip-b32",
        dir_name="clip-vit-base-patch32",
        hf_id="openai/clip-vit-base-patch32",
        modelscope_id=None,
        family="clip",
        dimension=512,
        download_mb=582,
        languages="en",
        quality=1,
        speed="fastest",
    ),
    "siglip2-base": SearchModelSpec(
        key="siglip2-base",
        dir_name="siglip2-base-patch16-224",
        hf_id="google/siglip2-base-patch16-224",
        modelscope_id="google/siglip2-base-patch16-224",
        family="siglip",
        dimension=768,
        download_mb=800,
        languages="multilingual",
        quality=3,
        speed="medium",
    ),
    "chinese-clip-b16": SearchModelSpec(
        key="chinese-clip-b16",
        dir_name="chinese-clip-vit-base-patch16",
        hf_id="OFA-Sys/chinese-clip-vit-base-patch16",
        modelscope_id="OFA-Sys/chinese-clip-vit-base-patch16",
        family="chinese_clip",
        dimension=512,
        download_mb=700,
        languages="zh",
        quality=2,
        speed="fast",
    ),
}

def available_models() -> list[SearchModelSpec]:
    """Return the models offered by the UI, cheapest first."""

    return sorted(SEARCH_MODELS.values(), key=lambda spec: (spec.download_mb, spec.key))


def resolve_spec(key: str | None) -> SearchModelSpec:
    """Return the spec for *key*, falling back to the default on unknown input."""

    if key and key in SEARCH_MODELS:
        return SEARCH_MODELS[key]
    return SEARCH_MODELS[DEFAULT_SEARCH_MODEL_KEY]


def models_root(install_root: Path | None = None) -> Path:
    """Return ``<install>/extension/models`` for this checkout or install."""

    if install_root is not None:
        return Path(install_root) / "extension" / "models"
    from .model_paths import candidate_roots

    return candidate_roots()[0]


def model_dir(spec: SearchModelSpec, *, install_root: Path | None = None) -> Path:
    """Directory of *spec*, preferring an existing copy anywhere on disk."""

    if install_root is not None:
        return models_root(install_root) / spec.dir_name
    import os

    override = os.environ.get("IPHOTO_CLIP_MODEL_DIR")
    if override:
        return Path(override).expanduser() / spec.dir_name
    from .model_paths import resolve_dir

    return resolve_dir(spec.dir_name)


def is_installed(spec: SearchModelSpec, *, install_root: Path | None = None) -> bool:
    """True when config **and** weights are on disk.

    `config.json` alone is not enough: an interrupted download leaves the config
    behind without any tensors, and the encoder would then fail at load time with
    a confusing error instead of offering the download again.
    """

    directory = model_dir(spec, install_root=install_root)
    if not (directory / "config.json").is_file():
        return False
    return (
        (directory / "model.safetensors").is_file()
        or (directory / "pytorch_model.bin").is_file()
        or any(directory.glob("*.safetensors"))
        or any(directory.glob("pytorch_model*.bin"))
    )


def installed_models(*, install_root: Path | None = None) -> list[SearchModelSpec]:
    return [spec for spec in available_models() if is_installed(spec, install_root=install_root)]


__all__ = [
    "DEFAULT_SEARCH_MODEL_KEY",
    "SEARCH_MODELS",
    "SETTINGS_KEY",
    "SearchModelSpec",
    "available_models",
    "installed_models",
    "is_installed",
    "model_dir",
    "models_root",
    "resolve_spec",
]
