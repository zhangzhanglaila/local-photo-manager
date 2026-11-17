"""CLIP model downloader."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)

# CLIP model ID
_MODEL_ID = "openai/clip-vit-base-patch32"


def get_model_dir(library_root: Path) -> Path:
    """Get the model directory for CLIP.

    Resolution order (same rule as the face models and the map extension):

    1. ``IPHOTO_CLIP_MODEL_DIR`` — explicit override;
    2. the packaged location ``<install>/extension/models``;
    3. the source checkout ``<repo>/extension/models``.

    The old code hardcoded ``parents[4]``, which resolves *above* the install
    directory in a standalone build, so a packaged app could never find the model.
    """

    override = os.environ.get("IPHOTO_CLIP_MODEL_DIR")
    if override:
        return Path(override).expanduser()

    module_path = Path(__file__).resolve()
    # Source checkout: <repo>/src/iPhoto/... → parents[3] is ``src``.
    # Standalone build: <install>/iPhoto/... → parents[3] is the install root.
    install_root = module_path.parents[3]
    if install_root.name.lower() == "src":
        install_root = module_path.parents[4]

    # An empty ``clip-vit-base-patch32`` directory exists in the checkout as a leftover,
    # so require an actual model file rather than "the directory exists".
    candidates = [install_root, module_path.parents[4], module_path.parents[3]]
    for root in candidates:
        model_dir = root / "extension" / "models"
        if (model_dir / "clip-vit-base-patch32" / "config.json").is_file():
            return model_dir
    return install_root / "extension" / "models"


def get_model_path(library_root: Path) -> Path:
    """Get the full path to the CLIP model."""
    return get_model_dir(library_root) / "clip-vit-base-patch32"


def is_model_available(model_dir: Path) -> bool:
    """Check if CLIP model is available."""
    model_path = model_dir / "clip-vit-base-patch32"
    config_file = model_path / "config.json"
    # Check for model weights (pytorch_model.bin or model.safetensors)
    model_weights = model_path / "pytorch_model.bin"
    model_safetensors = model_path / "model.safetensors"
    return config_file.exists() and (model_weights.exists() or model_safetensors.exists())


def download_model(
    model_dir: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """Download CLIP model.

    Parameters
    ----------
    model_dir : Path
        Directory to save the model.
    progress_callback : Optional[Callable]
        Callback for progress updates.

    Returns
    -------
    bool
        True if download was successful.
    """
    model_path = model_dir / "clip-vit-base-patch32"
    model_path.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(0, 100, "准备下载...")

    # Check if huggingface_hub is installed
    try:
        import huggingface_hub
    except ImportError:
        _LOGGER.error("huggingface_hub not installed")
        if progress_callback:
            progress_callback(0, 100, "错误: huggingface_hub 未安装")
        return False

    # Try using huggingface_hub with mirror
    try:
        if progress_callback:
            progress_callback(5, 100, "正在连接 HuggingFace 镜像...")
        return _download_with_huggingface_hub(model_path, progress_callback)
    except Exception as e:
        _LOGGER.warning("HuggingFace download failed: %s", e)
        if progress_callback:
            progress_callback(0, 100, f"HuggingFace 下载失败: {str(e)[:50]}")

    # Try using modelscope
    try:
        import modelscope
        if progress_callback:
            progress_callback(5, 100, "正在尝试 ModelScope...")
        return _download_with_modelscope(model_dir, progress_callback)
    except ImportError:
        _LOGGER.warning("modelscope not installed")
    except Exception as e:
        _LOGGER.warning("ModelScope download failed: %s", e)

    return False


def _download_with_huggingface_hub(
    model_path: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """Download using huggingface_hub with mirror support."""
    from huggingface_hub import snapshot_download

    # Try mirror first
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    if progress_callback:
        progress_callback(10, 100, "正在从镜像下载...")

    _LOGGER.info("Downloading CLIP model to %s", model_path)

    snapshot_download(
        repo_id=_MODEL_ID,
        local_dir=str(model_path),
        local_dir_use_symlinks=False,
    )

    if progress_callback:
        progress_callback(100, 100, "下载完成！")

    return True


def _download_with_modelscope(
    model_dir: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """Download using ModelScope (Chinese mirror)."""
    from modelscope import snapshot_download

    if progress_callback:
        progress_callback(10, 100, "正在从 ModelScope 下载...")

    # Try different model IDs on ModelScope
    model_ids = [
        "AI-ModelScope/clip-vit-base-patch32",
        "damo/clip_vit-base-patch32",
    ]

    for model_id in model_ids:
        try:
            snapshot_download(
                model_id=model_id,
                cache_dir=str(model_dir),
            )
            if progress_callback:
                progress_callback(100, 100, "下载完成！")
            return True
        except Exception as e:
            _LOGGER.warning("ModelScope download failed for %s: %s", model_id, e)
            continue

    return False


def get_download_instructions(model_dir: Path) -> str:
    """Get instructions for manually downloading the model."""
    model_path = model_dir / "clip-vit-base-patch32"

    return f"""手动下载 CLIP 模型：

【方法1】HuggingFace 镜像（推荐）
========================================
1. 打开 CMD 或 PowerShell
2. 执行以下命令：

pip install huggingface_hub

set HF_ENDPOINT=https://hf-mirror.com

python -c "from huggingface_hub import snapshot_download; snapshot_download('openai/clip-vit-base-patch32', local_dir=r'{model_path}')"

如果还是失败，尝试设置代理：
set HTTP_PROXY=http://127.0.0.1:7890
set HTTPS_PROXY=http://127.0.0.1:7890


【方法2】手动下载文件
========================================
1. 浏览器打开：https://hf-mirror.com/openai/clip-vit-base-patch32
2. 下载以下文件：
   - config.json
   - model.safetensors
   - preprocessor_config.json
   - tokenizer.json
   - vocab.json
   - merges.txt
   - special_tokens_map.json
   - tokenizer_config.json
3. 全部放到：{model_path}


【方法3】使用 Git 克隆
========================================
git clone https://hf-mirror.com/openai/clip-vit-base-patch32 "{model_path}"


下载位置：{model_path}
下载完成后重启应用即可使用语义搜索功能。
"""
