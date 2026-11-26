"""Download the weights for a search model.

Primary path: stream the files from HuggingFace over plain HTTP, which gives a
real byte-level percentage (``snapshot_download`` only exposes per-file tqdm
bars). If that fails — blocked network, missing file list — the ModelScope
mirror is tried, and finally ``snapshot_download`` itself.

Everything lands in ``extension/models/<dir>``; the rest of the app only checks
for ``config.json`` and never cares where the bytes came from.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Iterable
from urllib.request import Request, urlopen

from ..services import search_models

_LOGGER = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api/models/{repo}"
HF_FILE = "https://huggingface.co/{repo}/resolve/main/{name}"
MODELSCOPE_API = "https://modelscope.cn/api/v1/models/{repo}/repo/files"


class SearchModelDownloadError(RuntimeError):
    """Raised when no source could provide the model."""

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


ProgressCallback = Callable[[float | None, str], None]


def model_page_urls(spec: search_models.SearchModelSpec) -> list[tuple[str, str]]:
    """Return ``(label, url)`` pairs for the manual download pages."""

    urls = [("HuggingFace", f"https://huggingface.co/{spec.hf_id}")]
    if spec.modelscope_id:
        urls.append(("ModelScope", f"https://modelscope.cn/models/{spec.modelscope_id}"))
    return urls


def _needed_file(name: str) -> bool:
    """Only the files the encoder actually loads."""

    lowered = name.lower()
    if lowered.endswith((".md", ".txt")) and lowered != "merges.txt":
        return False
    if lowered.startswith("."):
        return False
    return lowered.endswith(
        (
            ".json",
            ".safetensors",
            ".bin",
            ".model",
            ".txt",
            ".spm",
        )
    )


def _http_json(url: str, opener) -> object:
    request = Request(url, headers={"User-Agent": "iPhoto/1.0"})
    with opener(request, timeout=20) as response:  # type: ignore[misc]
        return json.loads(response.read().decode("utf-8"))


def _remote_size(url: str, opener) -> int:
    request = Request(url, method="HEAD", headers={"User-Agent": "iPhoto/1.0"})
    try:
        with opener(request, timeout=20) as response:  # type: ignore[misc]
            return int(response.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001 - size is only used for the percentage
        return 0


def _list_hf_files(spec: search_models.SearchModelSpec, opener) -> list[tuple[str, int]]:
    payload = _http_json(HF_API.format(repo=spec.hf_id), opener)
    siblings = payload.get("siblings", []) if isinstance(payload, dict) else []
    names = [str(entry.get("rfilename", "")) for entry in siblings if isinstance(entry, dict)]
    selected = [name for name in names if name and _needed_file(name)]
    if not selected:
        raise SearchModelDownloadError(
            "HuggingFace did not list any model files", reason="no_file_list"
        )
    return [(name, _remote_size(HF_FILE.format(repo=spec.hf_id, name=name), opener)) for name in selected]


def _stream_file(url: str, target: Path, opener, on_chunk) -> None:
    request = Request(url, headers={"User-Agent": "iPhoto/1.0"})
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with opener(request, timeout=60) as response, temp_path.open("wb") as sink:  # type: ignore[misc]
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                sink.write(chunk)
                on_chunk(len(chunk))
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def _download_from_huggingface(
    spec: search_models.SearchModelSpec,
    target: Path,
    *,
    opener=urlopen,
    progress: ProgressCallback | None = None,
) -> None:
    files = _list_hf_files(spec, opener)
    total = sum(size for _name, size in files) or 0
    state = {"done": 0}

    def on_chunk(count: int) -> None:
        state["done"] += count
        if progress is None:
            return
        fraction = min(1.0, state["done"] / total) if total else None
        progress(fraction, "")

    for name, _size in files:
        if progress is not None:
            fraction = min(1.0, state["done"] / total) if total else None
            progress(fraction, name)
        _stream_file(
            HF_FILE.format(repo=spec.hf_id, name=name),
            target / name,
            opener,
            on_chunk,
        )
    if progress is not None:
        progress(1.0, "")


def download_search_model(
    spec: search_models.SearchModelSpec,
    *,
    install_root: Path | None = None,
    progress: ProgressCallback | None = None,
    opener=urlopen,
) -> Path:
    """Ensure *spec* is on disk and return its directory."""

    target = search_models.model_dir(spec, install_root=install_root)
    if search_models.is_installed(spec, install_root=install_root):
        return target

    target.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def report(fraction: float | None, message: str) -> None:
        if progress is not None:
            try:
                progress(fraction, message)
            except Exception:  # pragma: no cover - a bad callback must not abort
                pass

    report(0.0, "HuggingFace")
    try:
        _download_from_huggingface(spec, target, opener=opener, progress=report)
        if search_models.is_installed(spec, install_root=install_root):
            return target
        failures.append("HuggingFace: 文件不完整")
    except Exception as exc:  # noqa: BLE001 - every source reports its own error
        _LOGGER.warning("HuggingFace download failed for %s: %s", spec.hf_id, exc)
        failures.append(f"HuggingFace: {_short_error(exc)}")

    report(None, "ModelScope")
    try:
        _download_from_modelscope(spec, target)
        if search_models.is_installed(spec, install_root=install_root):
            return target
        failures.append("ModelScope: 文件不完整")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("ModelScope download failed for %s: %s", spec.modelscope_id, exc)
        failures.append(f"ModelScope: {_short_error(exc)}")

    raise SearchModelDownloadError(
        "；".join(failures) or "没有可用的下载源",
        reason=_reason_for(failures),
    )


def _short_error(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:160]


def _reason_for(failures: Iterable[str]) -> str:
    joined = " ".join(failures).lower()
    if "timed out" in joined or "timeout" in joined or "10060" in joined:
        return "network_timeout"
    if "name or service" in joined or "getaddrinfo" in joined or "11001" in joined:
        return "dns"
    if "connection" in joined or "unreachable" in joined or "10054" in joined:
        return "network"
    if "403" in joined or "401" in joined:
        return "auth"
    return "unknown"


def _download_from_modelscope(spec: search_models.SearchModelSpec, target: Path) -> None:
    from modelscope import snapshot_download as modelscope_download

    modelscope_download(spec.modelscope_id, local_dir=str(target))


__all__ = [
    "SearchModelDownloadError",
    "download_search_model",
    "model_page_urls",
]
