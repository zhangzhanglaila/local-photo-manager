"""A second launch must focus the running window, not start a second process."""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("PySide6.QtNetwork", reason="Qt network is required")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iPhoto.gui import single_instance as module  # noqa: E402


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
        if first._server is not None:
            first._server.close()


def test_second_instance_defers_to_the_first(qapp, key):
    first = module.SingleInstanceGuard(key)
    second = module.SingleInstanceGuard(key)
    try:
        assert first.try_acquire() is True
        # A second process must not take over the channel.
        assert second.try_acquire() is False
    finally:
        if first._server is not None:
            first._server.close()


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
        if first._server is not None:
            first._server.close()


def test_stale_channel_from_a_crashed_run_is_reclaimed(qapp, key):
    crashed = module.SingleInstanceGuard(key)
    assert crashed.try_acquire() is True
    # Simulate a kill: the listening socket disappears without a clean shutdown.
    crashed._server.close()

    restarted = module.SingleInstanceGuard(key)
    try:
        assert restarted.try_acquire() is True
    finally:
        if restarted._server is not None:
            restarted._server.close()


def test_environment_override_allows_a_second_copy(qapp, key, monkeypatch):
    monkeypatch.setenv("IPHOTO_ALLOW_MULTIPLE", "1")
    first = module.SingleInstanceGuard(key)
    second = module.SingleInstanceGuard(key)
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is True
    finally:
        for guard in (first, second):
            if guard._server is not None:
                guard._server.close()


def test_channel_name_is_per_user():
    name = module.instance_key()

    assert name.startswith("iPhoto-single-instance-")
    assert " " not in name
