"""Downloading a search model: source fallback and failure reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.agent.infrastructure import search_model_download
from iPhoto.agent.services import search_models


def _spec() -> search_models.SearchModelSpec:
    return search_models.resolve_spec("siglip2-base")


def test_existing_model_is_not_downloaded(tmp_path: Path, monkeypatch):
    spec = _spec()
    target = search_models.model_dir(spec, install_root=tmp_path)
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(
        search_model_download, "_download_from_huggingface", lambda *a: calls.append("hf")
    )

    result = search_model_download.download_search_model(spec, install_root=tmp_path)

    assert result == target
    assert calls == []


def test_huggingface_is_tried_first(tmp_path: Path, monkeypatch):
    spec = _spec()
    target = search_models.model_dir(spec, install_root=tmp_path)

    def fake_hf(_spec, directory):
        (directory / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(search_model_download, "_download_from_huggingface", fake_hf)
    monkeypatch.setattr(
        search_model_download,
        "_download_from_modelscope",
        lambda *a: pytest.fail("ModelScope should not be needed"),
    )

    assert search_model_download.download_search_model(spec, install_root=tmp_path) == target


def test_modelscope_is_the_fallback(tmp_path: Path, monkeypatch):
    spec = _spec()
    target = search_models.model_dir(spec, install_root=tmp_path)

    def failing_hf(_spec, _directory):
        raise OSError("huggingface.co unreachable")

    def fake_modelscope(_spec, directory):
        (directory / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(search_model_download, "_download_from_huggingface", failing_hf)
    monkeypatch.setattr(search_model_download, "_download_from_modelscope", fake_modelscope)

    assert search_model_download.download_search_model(spec, install_root=tmp_path) == target


def test_both_sources_failing_reports_both(tmp_path: Path, monkeypatch):
    spec = _spec()

    monkeypatch.setattr(
        search_model_download,
        "_download_from_huggingface",
        lambda *a: (_ for _ in ()).throw(OSError("hf down")),
    )
    monkeypatch.setattr(
        search_model_download,
        "_download_from_modelscope",
        lambda *a: (_ for _ in ()).throw(ImportError("modelscope not installed")),
    )

    with pytest.raises(search_model_download.SearchModelDownloadError) as info:
        search_model_download.download_search_model(spec, install_root=tmp_path)

    message = str(info.value)
    assert "HuggingFace" in message and "ModelScope" in message
