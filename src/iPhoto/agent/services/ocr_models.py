"""OCR model packs.

RapidOCR ships one pack inside the wheel (PP-OCRv3, detection + recognition +
orientation). Extra packs are discovered on disk so a user can drop a newer
PP-OCRv4 (or a language-specific recogniser) into ``extension/models/ocr-<name>``
with its ONNX files and pick it in 设置 → 智能 → OCR 模型.

No download is attempted: OCR packs come from different places than the search
models, and a half-downloaded pack would silently degrade recognition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: Settings key written by the settings window and read by the OCR engine.
SETTINGS_KEY = "agent.ocr.model"

BUILTIN_KEY = "ppocr-v3"
BUILTIN_TITLE = "RapidOCR PP-OCRv3"
BUILTIN_MB = 14


@dataclass(frozen=True)
class OCRModelSpec:
    """One OCR pack: either the bundled models or a folder on disk."""

    key: str
    title: str
    total_mb: float
    directory: Optional[Path] = None
    det: Optional[Path] = None
    rec: Optional[Path] = None
    cls: Optional[Path] = None

    @property
    def builtin(self) -> bool:
        return self.directory is None

    @property
    def summary(self) -> str:
        size = f"{int(round(self.total_mb))} MB"
        return size if not self.builtin else f"{size} · {_bundled_label()}"

    @property
    def languages(self) -> str:
        return "zh" if self.builtin else self.key


def _bundled_label() -> str:
    return "内置"


def models_root() -> Path:
    return Path(__file__).resolve().parents[3] / "extension" / "models"


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
    """Return the bundled pack plus any packs found on disk."""

    specs: list[OCRModelSpec] = [
        OCRModelSpec(key=BUILTIN_KEY, title=BUILTIN_TITLE, total_mb=BUILTIN_MB)
    ]
    root = models_root()
    if root.is_dir():
        for directory in sorted(root.glob("ocr-*")):
            if not directory.is_dir():
                continue
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
    """The bundled pack is always there; disk packs must still exist."""

    if spec.builtin:
        return True
    return spec.det is not None and spec.rec is not None and spec.det.exists()


_active_key: Optional[str] = None


def set_active_model(key: str | None) -> OCRModelSpec:
    global _active_key
    spec = resolve_spec(key)
    _active_key = spec.key
    return spec


def active_model() -> OCRModelSpec:
    return resolve_spec(_active_key)


__all__ = [
    "BUILTIN_KEY",
    "BUILTIN_MB",
    "BUILTIN_TITLE",
    "OCRModelSpec",
    "SETTINGS_KEY",
    "active_model",
    "available_models",
    "is_installed",
    "models_root",
    "resolve_spec",
    "set_active_model",
]
