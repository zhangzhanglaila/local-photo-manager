"""Location matching for search: the geocoder returns latin names, users type Chinese."""

from __future__ import annotations

from pathlib import Path

from iPhoto.agent.services import place_aliases
from iPhoto.gui.coordinators import main_coordinator


def test_chinese_place_names_become_pinyin_needles():
    needles = place_aliases.aliases_for("武汉科技大学")

    assert "wuhan" in needles
    # The raw query stays available so English/pinyin queries keep working.
    assert "武汉科技大学" in needles


def test_english_queries_pass_through():
    assert place_aliases.aliases_for("tokyo") == ["tokyo"]


def test_unknown_text_has_only_the_raw_needle():
    assert place_aliases.aliases_for("红色的花") == ["红色的花"]


def test_province_aliases_are_included():
    needles = place_aliases.aliases_for("新疆")

    assert "xinjiang" in needles


def test_location_match_uses_aliases(monkeypatch):
    index = [
        ("a1", "wuhan — hubei"),
        ("a2", "harbin — heilongjiang sheng"),
        ("a3", "tokyo"),
    ]
    monkeypatch.setattr(
        main_coordinator, "_build_location_index", lambda root, rows=None: index
    )

    hits = main_coordinator._match_location_hits(Path("."), "武汉", None)
    assert [asset_id for asset_id, _label in hits] == ["a1"]

    hits = main_coordinator._match_location_hits(Path("."), "东京", None)
    assert [asset_id for asset_id, _label in hits] == ["a3"]

    assert main_coordinator._match_location_hits(Path("."), "不存在的城市", None) == []


def test_short_queries_are_ignored(monkeypatch):
    monkeypatch.setattr(
        main_coordinator,
        "_build_location_index",
        lambda root, rows=None: [("a1", "wuhan — hubei")],
    )

    assert main_coordinator._match_location_hits(Path("."), "武", None) == []
