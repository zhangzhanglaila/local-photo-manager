"""Model choice lives in one injected object, not in three module globals."""

from __future__ import annotations

from iPhoto.agent.services import face_models, ocr_models, search_models
from iPhoto.agent.services.model_selection import ModelSelection


class _Settings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_registries_no_longer_remember_a_choice() -> None:
    for registry in (search_models, face_models, ocr_models):
        assert not hasattr(registry, "set_active_model")
        assert not hasattr(registry, "active_model")
        assert not hasattr(registry, "_active_key")


def test_defaults_per_feature() -> None:
    selection = ModelSelection()

    assert selection.key("search") == search_models.DEFAULT_SEARCH_MODEL_KEY
    assert selection.key("face") == face_models.DEFAULT_KEY
    assert selection.key("ocr") == ocr_models.BUILTIN_KEY


def test_applying_a_key_changes_only_that_feature() -> None:
    selection = ModelSelection()

    selection.apply("search", "siglip2-base")

    assert selection.key("search") == "siglip2-base"
    assert selection.key("face") == face_models.DEFAULT_KEY
    assert selection.spec("search").dimension == 768


def test_unknown_keys_fall_back_to_the_default() -> None:
    selection = ModelSelection()

    selection.apply("search", "not-a-model")

    assert selection.key("search") == search_models.DEFAULT_SEARCH_MODEL_KEY


def test_settings_are_the_only_persisted_source() -> None:
    settings = _Settings(
        {
            search_models.SETTINGS_KEY: "chinese-clip-b16",
            face_models.SETTINGS_KEY: "buffalo_s",
            ocr_models.SETTINGS_KEY: ocr_models.BUILTIN_KEY,
        }
    )
    selection = ModelSelection()

    selection.load_from_settings(settings)

    assert selection.key("search") == "chinese-clip-b16"
    assert selection.key("face") == "buffalo_s"
    assert selection.key("ocr") == ocr_models.BUILTIN_KEY


def test_settings_key_per_feature() -> None:
    selection = ModelSelection()

    assert selection.settings_key("search") == search_models.SETTINGS_KEY
    assert selection.settings_key("face") == face_models.SETTINGS_KEY
    assert selection.settings_key("ocr") == ocr_models.SETTINGS_KEY
