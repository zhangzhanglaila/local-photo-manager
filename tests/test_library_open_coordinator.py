"""Opening an album must create a library session before navigation sees it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.gui.coordinators.library_open_coordinator import LibraryOpenCoordinator


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    def show_message(self, message: str, timeout: int = 0) -> None:
        self.messages.append((message, timeout))


class _Navigation:
    def __init__(self) -> None:
        self.opened: list[Path] = []

    def open_album(self, path: Path) -> None:
        self.opened.append(path)


class _Facade:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.errorRaised = SimpleNamespace(emit=self.errors.append)


class _Dialog:
    def __init__(self, path: Path | None) -> None:
        self._path = path

    def open_album_dialog(self):
        return self._path


class _Context:
    def __init__(self, root: Path | None = None) -> None:
        self.library = SimpleNamespace(root=lambda: root)
        self.settings = SimpleNamespace()
        self.opened: list[Path] = []
        self.open_error: Exception | None = None
        self.wizard_shown = 0

    def show_welcome_wizard(self) -> None:
        self.wizard_shown += 1

    def open_library(self, path: Path) -> None:
        if self.open_error is not None:
            raise self.open_error
        self.opened.append(path)


def _make(root: Path | None = None, *, dialog_path: Path | None = None):
    context = _Context(root)
    navigation = _Navigation()
    status = _StatusBar()
    facade = _Facade()
    changed: list[bool] = []
    coordinator = LibraryOpenCoordinator(
        SimpleNamespace(),
        context,
        dialog=_Dialog(dialog_path),
        navigation=navigation,
        status_bar=status,
        facade=facade,
        library_root_getter=lambda: root,
        on_library_changed=lambda: changed.append(True),
    )
    return coordinator, context, navigation, status, facade, changed


def test_existing_root_descendant_opens_without_rebinding(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    album = root / "Trip"
    album.mkdir(parents=True)
    coordinator, context, navigation, _status, _facade, changed = _make(root)

    coordinator.open_album_from_path(album)

    assert context.opened == []
    assert navigation.opened == [album]
    assert changed == []


def test_outside_root_opens_a_session_first(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    other = tmp_path / "Other"
    other.mkdir()
    coordinator, context, navigation, _status, _facade, changed = _make(root)

    coordinator.open_album_from_path(other)

    assert context.opened == [other]
    assert changed == [True]
    assert navigation.opened == [other]


def test_open_failure_is_reported_and_does_not_navigate(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    other = tmp_path / "Other"
    other.mkdir()
    coordinator, context, navigation, _status, facade, changed = _make(root)
    context.open_error = RuntimeError("cannot open")

    coordinator.open_album_from_path(other)

    assert facade.errors == ["cannot open"]
    assert changed == []
    assert navigation.opened == []


def test_missing_path_is_still_handed_to_navigation(tmp_path: Path) -> None:
    missing = tmp_path / "Missing"
    coordinator, context, navigation, _status, _facade, _changed = _make(None)

    coordinator.open_album_from_path(missing)

    assert context.opened == []
    assert navigation.opened == [missing]


def test_dialog_selection_opens_the_album(tmp_path: Path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    coordinator, _context, navigation, _status, _facade, _changed = _make(
        None, dialog_path=album
    )

    coordinator.handle_open_album_dialog()

    assert navigation.opened == [album]


def test_setup_wizard_and_workspace_change_are_library_level(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    coordinator, context, _navigation, status, _facade, _changed = _make(root)
    monkeypatch.setattr(
        "iPhoto.gui.ui.controllers.workspace_relocation_controller.relocate_workspace_interactively",
        lambda window, root, settings: True,
    )

    coordinator.run_setup_wizard()
    coordinator.change_workspace()

    assert context.wizard_shown == 1
    assert status.messages == [("存储位置已更改，请重启应用以生效。", 8000)]
