"""Preference restore/persist glue is independent of file mutations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.gui.coordinators.window_preferences import WindowPreferences


class _Checked:
    def __init__(self) -> None:
        self.checked: bool | None = None

    def setChecked(self, value: bool) -> None:
        self.checked = value


class _Settings:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value


class _Playback:
    def __init__(self) -> None:
        self.face_names: bool | None = None

    def set_face_name_display_enabled(self, enabled: bool) -> None:
        self.face_names = enabled


class _Viewer:
    def __init__(self) -> None:
        self.wheel_action: str | None = None

    def set_wheel_action(self, action: str) -> None:
        self.wheel_action = action


class _PeoplePage:
    def __init__(self) -> None:
        self.hidden: bool | None = None

    def set_show_hidden_people(self, value: bool) -> None:
        self.hidden = value


class _VideoArea:
    def __init__(self) -> None:
        self.volume: int | None = None
        self.muted: bool | None = None

    def set_volume(self, value: int) -> None:
        self.volume = value

    def set_muted(self, value: bool) -> None:
        self.muted = value


class _Sidebar:
    def __init__(self) -> None:
        self.paths: set[str] | None = None

    def set_checked_root_paths(self, paths: set[str]) -> None:
        self.paths = paths


class _Gallery:
    def __init__(self) -> None:
        self.checked: list[Path] | None = None

    def set_checked_album_paths(self, paths: list[Path]) -> None:
        self.checked = paths

    def on_album_check_state_changed(self, paths: list[Path]) -> None:
        self.checked = paths


class _Search:
    def search_precision(self) -> str:
        return "balanced"


def _make(settings: _Settings):
    ui = SimpleNamespace(
        wheel_action_zoom=_Checked(),
        wheel_action_navigate=_Checked(),
        image_viewer=_Viewer(),
        toggle_face_names_action=_Checked(),
        toggle_hidden_people_action=_Checked(),
        people_page=_PeoplePage(),
        main_header=SimpleNamespace(
            toggle_semantic_search_action=_Checked(),
            search_precision_actions={"balanced": _Checked()},
        ),
        video_area=_VideoArea(),
        sidebar=_Sidebar(),
    )
    window = SimpleNamespace(ui=ui)
    playback = _Playback()
    gallery = _Gallery()
    context = SimpleNamespace(settings=settings, library=SimpleNamespace(roots=lambda: []))
    started: list[bool] = []
    preferences = WindowPreferences(
        window,
        context,
        playback=playback,
        search=_Search(),
        gallery_vm=gallery,
        start_embedding_generation=lambda: started.append(True),
    )
    return preferences, ui, playback, gallery, started


def test_restore_applies_every_stored_preference() -> None:
    settings = _Settings(
        {
            "ui.wheel_action": "zoom",
            "ui.show_face_names_in_detail": "true",
            "ui.show_hidden_people": True,
            "agent.semantic_search_enabled": "1",
            "ui.volume": "80",
            "ui.is_muted": "yes",
        }
    )
    preferences, ui, playback, _gallery, started = _make(settings)

    preferences.restore()

    assert ui.wheel_action_zoom.checked is True
    assert ui.image_viewer.wheel_action == "zoom"
    assert ui.toggle_face_names_action.checked is True
    assert playback.face_names is True
    assert ui.toggle_hidden_people_action.checked is True
    assert ui.people_page.hidden is True
    assert ui.main_header.toggle_semantic_search_action.checked is True
    assert ui.main_header.search_precision_actions["balanced"].checked is True
    assert started == [True]
    assert (ui.video_area.volume, ui.video_area.muted) == (80, True)


def test_restore_defaults_to_navigate_and_clamps_volume() -> None:
    settings = _Settings({"ui.volume": "not-a-number"})
    preferences, ui, playback, _gallery, started = _make(settings)

    preferences.restore()

    assert ui.wheel_action_navigate.checked is True
    assert ui.image_viewer.wheel_action == "navigate"
    assert playback.face_names is False
    assert ui.people_page.hidden is False
    assert started == []
    assert ui.video_area.volume == 75


def test_toggles_persist_only_when_the_value_changes() -> None:
    settings = _Settings(
        {
            "ui.wheel_action": "zoom",
            "ui.show_face_names_in_detail": True,
            "ui.show_hidden_people": True,
        }
    )
    preferences, ui, playback, _gallery, _started = _make(settings)

    preferences.on_wheel_action_changed(ui.wheel_action_zoom)
    assert settings.values["ui.wheel_action"] == "zoom"
    assert ui.image_viewer.wheel_action == "zoom"

    preferences.on_face_name_toggle_changed(True)
    assert playback.face_names is True
    preferences.on_hidden_people_toggle_changed(False)
    assert settings.values["ui.show_hidden_people"] is False
    assert ui.people_page.hidden is False


def test_checked_paths_keep_existing_roots_and_drop_stale_ones(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    nested = root / "Trip"
    nested.mkdir(parents=True)
    stale = tmp_path / "Gone"
    settings = _Settings(
        {"ui.checked_library_paths": [str(root), str(nested), str(stale)]}
    )
    preferences, ui, _playback, gallery, _started = _make(settings)
    preferences._context.library.roots = lambda: [root]

    preferences.restore_checked_library_paths()

    assert ui.sidebar.paths == {str(root), str(nested)}
    assert gallery.checked == [root, nested]
    assert settings.values["ui.checked_library_paths"] == [
        str(root),
        str(nested),
        str(stale),
    ]


def test_checked_paths_are_cleared_when_all_roots_are_gone(tmp_path: Path) -> None:
    stale = tmp_path / "Gone"
    settings = _Settings({"ui.checked_library_paths": [str(stale)]})
    preferences, ui, _playback, gallery, _started = _make(settings)
    preferences._context.library.roots = lambda: []

    preferences.restore_checked_library_paths()

    assert settings.values["ui.checked_library_paths"] == []
    assert ui.sidebar.paths is None
    assert gallery.checked is None


def test_checkbox_changes_are_persisted_and_forwarded(tmp_path: Path) -> None:
    settings = _Settings()
    preferences, _ui, _playback, gallery, _started = _make(settings)
    selected = [tmp_path / "A"]

    preferences.on_album_check_state_changed(selected)

    assert settings.values["ui.checked_library_paths"] == [str(selected[0])]
    assert gallery.checked == selected
