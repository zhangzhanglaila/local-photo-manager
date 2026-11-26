"""Smart-album names and the embedding status line come from i18n.

Both used to be f-strings with Chinese prose baked into the service. That made the
English UI show Chinese, and it put display copy inside code that has no idea how
the app is configured.
"""

from __future__ import annotations

from pathlib import Path

from iPhoto.agent.services.organize_service import OrganizeService
from iPhoto.i18n import set_language, tr


def _service() -> OrganizeService:
    # The grouping helpers only read rows; the repository and root are untouched.
    return OrganizeService(asset_repository=object(), library_root=Path("/library"))


def test_month_albums_use_the_translated_name() -> None:
    assets = [
        {"id": f"asset-{index}", "dt": "2024-03-05T10:00:00"}
        for index in range(5)
    ]

    albums = _service()._group_by_time(assets)

    assert [album.name for album in albums] == [
        tr("organize.smart_album.month_name", period="2024-03")
    ]
    assert albums[0].description == tr(
        "organize.smart_album.month_description", period="2024-03"
    )


def test_location_albums_use_the_translated_name() -> None:
    assets = [
        {"id": f"asset-{index}", "location": "Munich"} for index in range(3)
    ]

    albums = _service()._group_by_location(assets)

    assert albums[0].name == tr(
        "organize.smart_album.location_name", location="Munich"
    )


def test_event_albums_use_the_translated_name_and_range() -> None:
    assets = [
        {"id": f"asset-{index}", "dt": "2024-05-01T10:00:00", "ts": index}
        for index in range(5)
    ]

    albums = _service()._group_by_event(assets)

    assert albums[0].name == tr(
        "organize.smart_album.event_name", index=1, start="2024-05-01"
    )


def test_names_follow_the_active_language() -> None:
    assets = [
        {"id": f"asset-{index}", "dt": "2024-03-05T10:00:00"}
        for index in range(5)
    ]
    set_language("en")
    try:
        albums = _service()._group_by_time(assets)
    finally:
        set_language("zh")

    assert albums[0].name == "Photos from 2024-03"
