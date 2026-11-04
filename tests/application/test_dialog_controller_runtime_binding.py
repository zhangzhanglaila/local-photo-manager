from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from iPhoto.gui.ui.controllers.dialog_controller import DialogController


class _Library:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self.bind_calls: list[Path] = []
        self.roots_added: list[Path] = []
        self.scan_requests: list[tuple[Path, list[str], list[str]]] = []

    def root(self) -> Path | None:
        return self._root

    def roots(self) -> list[Path]:
        """The runtime controller exposes every bound root, not only the active one."""

        return [self._root] if self._root is not None else []

    def bind_path(self, root: Path) -> None:
        self.bind_calls.append(root)
        self._root = root

    def add_root(self, root: Path) -> None:
        self.roots_added.append(root)
        if self._root is None:
            self._root = root

    def is_scanning_path(self, _root: Path) -> bool:
        return False

    def start_scanning(
        self,
        root: Path,
        include: list[str],
        exclude: list[str],
    ) -> None:
        self.scan_requests.append((root, list(include), list(exclude)))


class _Context:
    def __init__(self, root: Path | None = None) -> None:
        self.library = _Library(root)
        self.facade = Mock()
        self.settings = Mock()
        self.open_calls: list[Path] = []

    def open_library(self, root: Path) -> object:
        self.open_calls.append(root)
        self.library._root = root
        return object()


def test_bind_library_dialog_adds_an_extra_root_when_one_is_already_bound(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Picking an unrelated folder while a library is bound adds a second root.

    Multi-root support changed this behaviour: an unrelated folder used to
    replace the current library through ``open_library``, and now coexists with
    it. The first bind still goes through ``open_library`` — see the test below.
    """

    old_root = tmp_path / "old"
    old_root.mkdir()
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    context = _Context(old_root)
    status_bar = Mock()
    controller = DialogController(object(), context, status_bar)

    monkeypatch.setattr(
        "iPhoto.gui.ui.controllers.dialog_controller.dialogs.select_directory",
        lambda *_args, **_kwargs: selected_root,
    )

    assert controller.bind_library_dialog() == selected_root

    assert context.library.roots_added == [selected_root]
    assert context.library.bind_calls == []
    assert context.open_calls == []
    context.facade.scan_root_async.assert_called_once_with(
        selected_root,
        include=DEFAULT_INCLUDE,
        exclude=DEFAULT_EXCLUDE,
    )


def test_bind_library_dialog_uses_runtime_open_library(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The first bind goes through the runtime ``open_library`` entry point."""

    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    context = _Context(None)
    status_bar = Mock()
    controller = DialogController(object(), context, status_bar)

    monkeypatch.setattr(
        "iPhoto.gui.ui.controllers.dialog_controller.dialogs.select_directory",
        lambda *_args, **_kwargs: selected_root,
    )

    assert controller.bind_library_dialog() == selected_root

    assert context.open_calls == [selected_root]
    assert context.library.roots_added == []
    context.facade.open_album.assert_called_once_with(selected_root)
    context.facade.scan_root_async.assert_called_once_with(
        selected_root,
        include=DEFAULT_INCLUDE,
        exclude=DEFAULT_EXCLUDE,
    )
    # Paths are persisted as a list plus the legacy single-path key.
    persisted = {call.args[0]: call.args[1] for call in context.settings.set.call_args_list}
    assert persisted["basic_library_paths"] == [str(selected_root)]
    assert persisted["basic_library_path"] == str(selected_root)
    # The status text is localised, so assert that the chosen folder is reported
    # rather than pinning an English string.
    status_bar.showMessage.assert_called_once()
    assert selected_root.name in status_bar.showMessage.call_args.args[0]
