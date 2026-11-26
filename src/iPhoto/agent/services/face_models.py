"""InsightFace model packs available for the face scan.

Both packs ship with the same five ONNX files but differ in size and accuracy:
``buffalo_s`` uses SCRFD-500M + MobileFaceNet, ``buffalo_l`` uses SCRFD-10G +
ResNet50. Which one runs is a user choice (设置 → 智能 → 人脸模型) instead of a
constant, so a slow machine can trade accuracy for speed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Settings key written by the settings window and read by the scan worker.
SETTINGS_KEY = "agent.face.model"


@dataclass(frozen=True)
class FaceModelSpec:
    """One InsightFace pack."""

    key: str
    title: str
    detect_mb: float
    recognise_mb: float
    quality: int  # 1-3, higher is more accurate
    speed: int  # 1-3, higher is faster

    @property
    def dir_name(self) -> str:
        return self.key

    @property
    def download_mb(self) -> int:
        return int(round(self.detect_mb + self.recognise_mb))

    @property
    def summary(self) -> str:
        return f"{self.download_mb} MB"


MODELS: tuple[FaceModelSpec, ...] = (
    FaceModelSpec(
        key="buffalo_l",
        title="InsightFace buffalo_l",
        detect_mb=16.1,
        recognise_mb=166.3,
        quality=3,
        speed=1,
    ),
    FaceModelSpec(
        key="buffalo_s",
        title="InsightFace buffalo_s",
        detect_mb=2.4,
        recognise_mb=13.0,
        quality=2,
        speed=3,
    ),
)

DEFAULT_KEY = "buffalo_l"

_active_key: Optional[str] = None


def models_root() -> Path:
    """Canonical model directory (see :mod:`model_paths`)."""

    from .model_paths import install_root

    return install_root()


def available_models() -> tuple[FaceModelSpec, ...]:
    return MODELS


def resolve_spec(key: str | None) -> FaceModelSpec:
    """Return the spec for *key*, falling back to the default."""

    for spec in MODELS:
        if spec.key == key:
            return spec
    return next(spec for spec in MODELS if spec.key == DEFAULT_KEY)


def model_dir(spec: FaceModelSpec) -> Path:
    """Directory of *spec*, preferring an existing copy anywhere on disk."""

    from .model_paths import resolve_dir

    return resolve_dir(spec.dir_name, patterns=("*.onnx",))


def is_installed(spec: FaceModelSpec) -> bool:
    """A pack counts as installed when the detector and recogniser are present."""

    directory = model_dir(spec)
    if not directory.is_dir():
        return False
    names = {path.name for path in directory.glob("*.onnx")}
    return any(name.startswith("det_") for name in names) and any(
        name.startswith("w600k") for name in names
    )


def active_key() -> str:
    """Return the key currently in use (before any settings read)."""

    return _active_key or DEFAULT_KEY


def set_active_model(key: str | None) -> FaceModelSpec:
    """Record the choice; the next scan uses it."""

    global _active_key
    spec = resolve_spec(key)
    if not is_installed(spec):
        # Never leave the scan pointed at a pack that is not on disk.
        fallback = next((item for item in MODELS if is_installed(item)), None)
        if fallback is not None:
            spec = fallback
    _active_key = spec.key
    return spec


def active_model() -> FaceModelSpec:
    return resolve_spec(_active_key)


__all__ = [
    "DEFAULT_KEY",
    "MODELS",
    "SETTINGS_KEY",
    "FaceModelSpec",
    "active_key",
    "active_model",
    "available_models",
    "is_installed",
    "model_dir",
    "models_root",
    "resolve_spec",
    "set_active_model",
]
