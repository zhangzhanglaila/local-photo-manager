"""Startup tasks: stale GPS cleanup and the one-time ExifTool warning."""

from __future__ import annotations

from types import SimpleNamespace

from iPhoto.gui.coordinators import startup_tasks
from iPhoto.gui.coordinators.startup_tasks import StartupTasks
from iPhoto.infrastructure.services import metadata_provider
from iPhoto.utils import exiftool


class _Repo:
    def __init__(self, count: int = 0, *, fail: bool = False) -> None:
        self.count = count
        self.fail = fail
        self.calls = 0

    def clear_location_for_null_gps(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("db locked")
        return self.count


def _make(repo=None):
    runtime = SimpleNamespace(repository=repo if repo is not None else _Repo())
    context = SimpleNamespace(asset_runtime=runtime)
    return StartupTasks(SimpleNamespace(), context)


def test_gps_cleanup_calls_the_repository() -> None:
    repo = _Repo(count=2)
    tasks = _make(repo)

    tasks._cleanup_null_gps_locations()

    assert repo.calls == 1


def test_gps_cleanup_swallows_repository_errors() -> None:
    tasks = _make(_Repo(fail=True))

    tasks._cleanup_null_gps_locations()


def test_exiftool_available_installs_callback_without_warning(monkeypatch) -> None:
    monkeypatch.setattr(exiftool, "_resolve_exiftool_executable", lambda: "/usr/bin/exiftool")
    monkeypatch.setattr(metadata_provider, "_exiftool_missing_notified", False)
    monkeypatch.setattr(metadata_provider, "_on_exiftool_missing", None)
    warnings: list[tuple] = []
    monkeypatch.setattr(
        startup_tasks,
        "tr",
        lambda key, **params: key,
    )
    from iPhoto.gui.ui.widgets import dialogs

    monkeypatch.setattr(dialogs, "show_warning", lambda *args, **kwargs: warnings.append(args))

    tasks = _make()
    tasks._wire_exiftool_missing_warning()

    assert warnings == []
    assert metadata_provider._on_exiftool_missing is not None
    assert metadata_provider._exiftool_missing_notified is False


def test_missing_exiftool_is_reported_once(monkeypatch) -> None:
    def fail():
        raise RuntimeError("not found")

    monkeypatch.setattr(exiftool, "_resolve_exiftool_executable", fail)
    monkeypatch.setattr(metadata_provider, "_exiftool_missing_notified", False)
    monkeypatch.setattr(metadata_provider, "_on_exiftool_missing", None)
    monkeypatch.setattr(startup_tasks, "tr", lambda key, **params: key)
    warnings: list[tuple] = []
    from iPhoto.gui.ui.widgets import dialogs

    monkeypatch.setattr(dialogs, "show_warning", lambda *args, **kwargs: warnings.append(args))

    tasks = _make()
    tasks._wire_exiftool_missing_warning()

    assert warnings
    assert metadata_provider._exiftool_missing_notified is True
