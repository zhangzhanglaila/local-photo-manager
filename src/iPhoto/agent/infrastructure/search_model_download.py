"""Download the weights for a search model.

Two sources are tried in order: HuggingFace (direct) and ModelScope (the mirror
that is reachable from mainland networks). Both write into the same
``extension/models/<dir>`` folder, so the rest of the app only checks for
``config.json`` and never cares where the files came from.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..services import search_models

_LOGGER = logging.getLogger(__name__)


class SearchModelDownloadError(RuntimeError):
    """Raised when no source could provide the model."""


def download_search_model(
    spec: search_models.SearchModelSpec,
    *,
    install_root: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Ensure *spec* is on disk and return its directory."""

    target = search_models.model_dir(spec, install_root=install_root)
    if search_models.is_installed(spec, install_root=install_root):
        return target

    target.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def report(message: str) -> None:
        _LOGGER.info(message)
        if progress is not None:
            try:
                progress(message)
            except Exception:  # pragma: no cover - a bad callback must not abort
                pass

    report(f"downloading {spec.hf_id} from HuggingFace")
    try:
        _download_from_huggingface(spec, target)
        if search_models.is_installed(spec, install_root=install_root):
            return target
        failures.append("HuggingFace: files incomplete")
    except Exception as exc:  # noqa: BLE001 - report every source's error
        _LOGGER.warning("HuggingFace download failed for %s: %s", spec.hf_id, exc)
        failures.append(f"HuggingFace: {exc}")

    if spec.modelscope_id:
        report(f"downloading {spec.modelscope_id} from ModelScope")
        try:
            _download_from_modelscope(spec, target)
            if search_models.is_installed(spec, install_root=install_root):
                return target
            failures.append("ModelScope: files incomplete")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("ModelScope download failed for %s: %s", spec.modelscope_id, exc)
            failures.append(f"ModelScope: {exc}")

    raise SearchModelDownloadError("; ".join(failures) or "no download source available")


def _download_from_huggingface(spec: search_models.SearchModelSpec, target: Path) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=spec.hf_id, local_dir=str(target))


def _download_from_modelscope(spec: search_models.SearchModelSpec, target: Path) -> None:
    from modelscope import snapshot_download as modelscope_download

    modelscope_download(spec.modelscope_id, local_dir=str(target))


__all__ = ["SearchModelDownloadError", "download_search_model"]
