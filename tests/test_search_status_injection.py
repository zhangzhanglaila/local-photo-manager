"""The search-index status is injected, not fetched from a global."""

from __future__ import annotations

from pathlib import Path

from iPhoto.agent.infrastructure.clip_embedding import CLIPEmbeddingService
from iPhoto.agent.services.ai_devices import search_device
from iPhoto.agent.services import search_status as search_status_module
from iPhoto.agent.services.search_status import SearchIndexStatus


def test_module_no_longer_exposes_a_process_wide_accessor() -> None:
    assert not hasattr(search_status_module, "search_status")


def test_two_status_objects_are_independent() -> None:
    first = SearchIndexStatus()
    second = SearchIndexStatus()

    first.set_indexing(True, 3, 10)

    assert first.running is True
    assert second.running is False
    assert second.total == 0


def test_encoder_keeps_the_injected_status(tmp_path: Path) -> None:
    status = SearchIndexStatus()

    service = CLIPEmbeddingService(model_dir=tmp_path, family="clip", status=status)

    assert service._status is status


def test_search_device_prefers_the_injected_status() -> None:
    class _Status:
        device = "cuda"

    assert search_device(_Status()) == "CUDA"
