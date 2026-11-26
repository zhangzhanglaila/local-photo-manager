"""A second launch must focus the running window, not start a second process."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtNetwork", reason="Qt network is required")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iPhoto.gui import single_instance as module  # noqa: E402

_SRC = Path(__file__).resolve().parents[1] / "src"

_LAUNCH_SCRIPT = """
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, {src!r})
from PySide6.QtWidgets import QApplication
app = QApplication([])
from iPhoto.gui.single_instance import SingleInstanceGuard
guard = SingleInstanceGuard({key!r})
owner = guard.try_acquire()
print("OWNER" if owner else "SECOND", flush=True)
if owner:
    time.sleep(float({hold!r}))
"""


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def key(monkeypatch):
    name = f"iphoto-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.delenv("IPHOTO_ALLOW_MULTIPLE", raising=False)
    return name


def test_first_instance_keeps_running(qapp, key):
    first = module.SingleInstanceGuard(key)
    try:
        assert first.try_acquire() is True
    finally:
        first.release()


def test_second_instance_defers_to_the_first(qapp, key):
    first = module.SingleInstanceGuard(key)
    second = module.SingleInstanceGuard(key)
    try:
        assert first.try_acquire() is True
        # A second process must not take over the lock.
        assert second.try_acquire() is False
    finally:
        second.release()
        first.release()


def test_running_instance_is_asked_to_show_itself(qapp, key):
    first = module.SingleInstanceGuard(key)
    second = module.SingleInstanceGuard(key)
    shown: list[int] = []
    try:
        assert first.try_acquire() is True
        first.show_requested.connect(lambda: shown.append(1))
        first.bind_window()

        assert second.try_acquire() is False
        # The request travels over the local socket, so it needs an event loop turn.
        for _ in range(50):
            qapp.processEvents()
            if shown:
                break
            qapp.thread().msleep(20)

        assert shown, "the running instance was never asked to show itself"
    finally:
        second.release()
        first.release()


def test_releasing_the_lock_lets_the_next_launch_win(qapp, key):
    crashed = module.SingleInstanceGuard(key)
    assert crashed.try_acquire() is True
    crashed.release()

    restarted = module.SingleInstanceGuard(key)
    try:
        assert restarted.try_acquire() is True
    finally:
        restarted.release()


def test_environment_override_allows_a_second_copy(qapp, key, monkeypatch):
    monkeypatch.setenv("IPHOTO_ALLOW_MULTIPLE", "1")
    first = module.SingleInstanceGuard(key)
    second = module.SingleInstanceGuard(key)
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is True
    finally:
        second.release()
        first.release()


def test_channel_name_is_per_user():
    name = module.instance_key()

    assert name.startswith("iPhoto-single-instance-")
    assert " " not in name


# --- real processes ---------------------------------------------------------


def _launch(key: str, hold: float) -> subprocess.Popen[str]:
    code = _LAUNCH_SCRIPT.format(src=str(_SRC), key=key, hold=hold)
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
    )


def _stop(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        process.terminate()
    for process in processes:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


def test_three_simultaneous_launches_yield_one_owner():
    """Three quick clicks raced through the old socket probe; the OS lock must not."""

    key = f"iphoto-race-{uuid.uuid4().hex[:10]}"
    processes = [_launch(key, hold=5.0) for _ in range(3)]
    outcomes: list[str] = []
    try:
        for process in processes:
            line = process.stdout.readline().strip() if process.stdout else ""
            outcomes.append(line)
    finally:
        _stop(processes)

    assert outcomes.count("OWNER") == 1, outcomes
    assert outcomes.count("SECOND") == 2, outcomes


def test_second_launch_exits_while_the_first_is_running():
    key = f"iphoto-race-{uuid.uuid4().hex[:10]}"
    first = _launch(key, hold=6.0)
    second = None
    try:
        assert first.stdout is not None
        assert first.stdout.readline().strip() == "OWNER"

        second = _launch(key, hold=0.0)
        assert second.stdout is not None
        assert second.stdout.readline().strip() == "SECOND"
        # It must not linger: the point is that the click costs one quick exit.
        assert second.wait(timeout=20) == 0
    finally:
        _stop([process for process in (first, second) if process is not None])
