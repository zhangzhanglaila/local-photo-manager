"""OCR model packs.

The default is **PP-OCRv5 mobile** (detection + recognition), the current
PaddleOCR generation, fetched by RapidOCR on first use into
``extension/models/rapidocr``. PP-OCRv3 is gone: the old
``rapidocr-onnxruntime`` wheel shipped 2021-era models and pinned its own
character dictionary, so it could not be upgraded in place.

Extra packs are still discovered on disk (``extension/models/ocr-<name>``) so a
language-specific recogniser can be dropped in and picked in 设置 → 智能.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Settings key written by the settings window and read by the OCR engine.
SETTINGS_KEY = "agent.ocr.model"

BUILTIN_KEY = "ppocr-v5"
BUILTIN_TITLE = "PP-OCRv5 mobile"
BUILTIN_VERSION = "PP-OCRv5"
BUILTIN_MODEL_TYPE = "mobile"
#: Detection + recognition + orientation, measured after the first download.
BUILTIN_MB = 21

#: Where RapidOCR keeps the downloaded ONNX files (shared across libraries).
RAPIDOCR_DIR_NAME = "rapidocr"


@dataclass(frozen=True)
class OCRModelSpec:
    """One OCR pack: either the built-in PP-OCRv5 or a folder on disk."""

    key: str
    title: str
    total_mb: float
    version: Optional[str] = None
    model_type: Optional[str] = None
    directory: Optional[Path] = None
    det: Optional[Path] = None
    rec: Optional[Path] = None
    cls: Optional[Path] = None

    @property
    def builtin(self) -> bool:
        return self.directory is None

    @property
    def summary(self) -> str:
        """Size only: the title already names the model generation."""

        return f"{int(round(self.total_mb))} MB"

    @property
    def languages(self) -> str:
        return "zh" if self.builtin else self.key


def models_root() -> Path:
    from .model_paths import install_root

    return install_root()


def rapidocr_dir() -> Path:
    """Directory holding the downloaded PP-OCRv5 files.

    Resolved rather than composed so an existing download in the legacy tree is
    reused instead of being fetched again.
    """

    from .model_paths import resolve_dir

    return resolve_dir(RAPIDOCR_DIR_NAME, patterns=("*.onnx",))


def _spec_from_directory(directory: Path) -> OCRModelSpec | None:
    """Build a spec from a folder of OCR models, or return ``None``."""

    files = {path.name.lower(): path for path in directory.glob("*.onnx")}
    if not files:
        return None

    def pick(*needles: str) -> Optional[Path]:
        for needle in needles:
            for name, path in files.items():
                if needle in name:
                    return path
        return None

    det = pick("det")
    rec = pick("rec")
    if det is None or rec is None:
        # A pack without detection or recognition cannot run a page.
        return None
    total_mb = sum(path.stat().st_size for path in files.values()) / 1e6
    return OCRModelSpec(
        key=directory.name,
        title=directory.name.replace("ocr-", "OCR ", 1),
        total_mb=total_mb,
        directory=directory,
        det=det,
        rec=rec,
        cls=pick("cls"),
    )


def available_models() -> tuple[OCRModelSpec, ...]:
    """Return the built-in PP-OCRv5 pack plus any packs found on disk."""

    specs: list[OCRModelSpec] = [
        OCRModelSpec(
            key=BUILTIN_KEY,
            title=BUILTIN_TITLE,
            total_mb=BUILTIN_MB,
            version=BUILTIN_VERSION,
            model_type=BUILTIN_MODEL_TYPE,
        )
    ]
    from .model_paths import existing_dirs

    for directory in existing_dirs("ocr-*"):
        spec = _spec_from_directory(directory)
        if spec is not None:
            specs.append(spec)
    return tuple(specs)


def resolve_spec(key: str | None) -> OCRModelSpec:
    specs = available_models()
    for spec in specs:
        if spec.key == key:
            return spec
    return specs[0]


def is_installed(spec: OCRModelSpec) -> bool:
    """Whether the ONNX files are already on disk."""

    if spec.builtin:
        directory = rapidocr_dir()
        if not directory.is_dir():
            return False
        names = {path.name.lower() for path in directory.glob("*.onnx")}
        return any("det" in name for name in names) and any(
            "rec" in name for name in names
        )
    return spec.det is not None and spec.rec is not None and spec.det.exists()


__all__ = [
    "BUILTIN_KEY",
    "BUILTIN_MB",
    "BUILTIN_MODEL_TYPE",
    "BUILTIN_TITLE",
    "BUILTIN_VERSION",
    "OCRModelSpec",
    "SETTINGS_KEY",
    "available_models",
    "is_installed",
    "models_root",
    "rapidocr_dir",
    "resolve_spec",
]
