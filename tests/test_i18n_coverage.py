"""Every user-visible string the UI looks up must exist in both languages."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iPhoto.i18n import set_language, tr

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "iPhoto"
_TR_CALL = re.compile(r'tr\("([\w.\-]+)"')


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for path in _SOURCE_ROOT.rglob("*.py"):
        if "i18n" in path.as_posix():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        keys.update(_TR_CALL.findall(text))
    return keys


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    set_language("zh")


def test_every_used_key_exists_in_both_languages():
    missing: dict[str, list[str]] = {}
    for language in ("zh", "en"):
        set_language(language)
        absent = sorted(key for key in _used_keys() if tr(key) == key)
        if absent:
            missing[language] = absent
    assert not missing, f"untranslated keys: {missing}"


def test_settings_dialog_labels_follow_the_language():
    set_language("en")
    assert tr("settings.page.library") == "Library"
    assert tr("settings.row.change_workspace") == "Change storage location…"

    set_language("zh")
    assert tr("settings.page.library") == "图库"


def test_empty_states_follow_the_language():
    set_language("en")
    assert tr("empty.videos") == "No videos"
    assert tr("empty.favorites") == "No favorites"
    assert tr("empty.no_media").startswith("No media found")

    set_language("zh")
    assert tr("empty.videos") == "没有视频"


def test_sidebar_static_nodes_follow_the_language():
    set_language("en")
    assert tr("sidebar.home") == "Home"
    assert tr("sidebar.albums") == "Albums"
    assert tr("sidebar.recently_deleted") == "Recently Deleted"

    set_language("zh")
    assert tr("sidebar.home") == "首页"


def test_dialog_defaults_follow_the_language():
    set_language("en")
    assert (tr("dialog.ok"), tr("dialog.cancel")) == ("OK", "Cancel")
    set_language("zh")
    assert (tr("dialog.ok"), tr("dialog.cancel")) == ("确定", "取消")
