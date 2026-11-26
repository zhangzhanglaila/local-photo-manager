"""Which model pack each AI feature uses — one owner instead of three globals.

Search, face and OCR each kept their active pack in a module-level ``_active_key``
that ``bootstrap`` poked at startup and the settings window poked on every switch.
That made the choice invisible: a second window, a headless session or a test could
not pick a different pack, and "what is active" depended on import order.

``ModelSelection`` is now a plain object created by ``bootstrap`` and injected into
the library session, the runtime controller, the settings window and the
coordinator. The registries (``search_models`` / ``face_models`` / ``ocr_models``)
are pure catalogues: they can resolve a key, list packs and report whether a pack is
installed, but they no longer remember a choice.
"""

from __future__ import annotations

from typing import Any

from . import face_models, ocr_models, search_models

FEATURES = ("search", "face", "ocr")


def _registry(feature: str):
    if feature == "search":
        return search_models
    if feature == "face":
        return face_models
    if feature == "ocr":
        return ocr_models
    raise ValueError(f"unknown AI feature: {feature!r}")


def _default_key(feature: str) -> str:
    if feature == "search":
        return search_models.DEFAULT_SEARCH_MODEL_KEY
    if feature == "face":
        return face_models.DEFAULT_KEY
    return ocr_models.BUILTIN_KEY


class ModelSelection:
    """The active pack per feature, with the fallbacks each registry used to own."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {feature: _default_key(feature) for feature in FEATURES}

    def key(self, feature: str) -> str:
        """Return the active key for *feature*."""

        return self._keys[feature]

    def spec(self, feature: str) -> Any:
        """Return the active spec for *feature*."""

        return _registry(feature).resolve_spec(self._keys[feature])

    def apply(self, feature: str, key: str | None) -> Any:
        """Make *key* active for *feature* and return its spec.

        Mirrors the old ``set_active_model`` semantics: an unknown key means the
        feature default, and the face scan never stays pointed at a pack that is not
        on disk (it falls back to any installed pack).
        """

        registry = _registry(feature)
        spec = registry.resolve_spec(key)
        if feature == "face" and not face_models.is_installed(spec):
            installed = next(
                (
                    item
                    for item in face_models.available_models()
                    if face_models.is_installed(item)
                ),
                None,
            )
            if installed is not None:
                spec = installed
        self._keys[feature] = spec.key
        return spec

    def load_from_settings(self, settings: Any) -> None:
        """Apply the choice stored in *settings* for every feature."""

        for feature in FEATURES:
            registry = _registry(feature)
            stored = settings.get(registry.SETTINGS_KEY, None) if settings is not None else None
            self.apply(feature, stored)

    def settings_key(self, feature: str) -> str:
        """Return the settings key that stores *feature*'s choice."""

        return _registry(feature).SETTINGS_KEY


__all__ = ["FEATURES", "ModelSelection"]
