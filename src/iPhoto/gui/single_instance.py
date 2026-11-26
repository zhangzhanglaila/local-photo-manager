"""Keep a single iPhoto process, the way a desktop app should behave.

Launching the app again (double-clicking the shortcut while it is already running)
used to start a second process: two windows, two taskbar buttons, two scans and
two SQLite writers on the same library. The second launch now asks the running
instance to come to the front and exits by itself.

The channel is a Qt local socket: a named pipe on Windows, a UNIX socket
elsewhere. No extra dependency, and the stale-socket case (the app was killed)
is handled by removing the leftover name before listening.
"""

from __future__ import annotations

import getpass
import logging
import os
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_LOGGER = logging.getLogger(__name__)

#: Message the second instance sends; anything else is ignored.
_SHOW_MESSAGE = b"show"


def instance_key() -> str:
    """Return the per-user channel name used to detect a running instance."""

    user = "default"
    try:
        user = getpass.getuser() or "default"
    except Exception:  # pragma: no cover - getpass can fail without a login name
        pass
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", user)
    return f"iPhoto-single-instance-{safe}"


def multiple_instances_allowed() -> bool:
    """Escape hatch for tests and for deliberately running two copies."""

    return os.environ.get("IPHOTO_ALLOW_MULTIPLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class SingleInstanceGuard(QObject):
    """Elect one process and deliver "show yourself" requests to it."""

    show_requested = Signal()

    def __init__(self, key: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key or instance_key()
        self._server: QLocalServer | None = None
        #: A request that arrived before the window existed.
        self._pending = False
        self._window_ready = False

    # ------------------------------------------------------------------
    @property
    def key(self) -> str:
        return self._key

    def try_acquire(self) -> bool:
        """Return ``True`` when this process is the one that should keep running."""

        if multiple_instances_allowed():
            _LOGGER.info("single-instance guard disabled by IPHOTO_ALLOW_MULTIPLE")
            return True

        if self._notify_running_instance():
            return False

        # Nobody answered: the previous run either never listened or was killed
        # and left the name behind. Clear it before claiming it.
        QLocalServer.removeServer(self._key)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._accept_connection)
        if not server.listen(self._key):
            # A guard that blocks startup is worse than a duplicate window.
            _LOGGER.warning(
                "could not listen on %s (%s); continuing without the guard",
                self._key,
                server.errorString(),
            )
            return True
        self._server = server
        _LOGGER.info("single-instance guard listening on %s", self._key)
        return True

    def _notify_running_instance(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(400):
            return False
        socket.write(_SHOW_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        _LOGGER.info("another iPhoto instance is running; asked it to show itself")
        return True

    def _accept_connection(self) -> None:
        server = self._server
        if server is None:  # pragma: no cover - defensive
            return
        connection = server.nextPendingConnection()
        if connection is None:  # pragma: no cover - defensive
            return
        state = {"handled": False}

        def handle() -> None:
            if state["handled"]:
                return
            state["handled"] = True
            self._read_request(connection)

        connection.readyRead.connect(handle)
        # The bytes may still be in flight when the connection is accepted (the
        # client writes and leaves immediately), so give them a moment instead of
        # waiting for a signal that may already have been delivered. Do not read
        # on ``disconnected``: that can arrive after Qt deleted the socket.
        if connection.bytesAvailable() <= 0:
            connection.waitForReadyRead(300)
        handle()
        connection.disconnected.connect(connection.deleteLater)

    def _read_request(self, connection: QLocalSocket) -> None:
        try:
            if connection.bytesAvailable() <= 0:
                payload: bytes = _SHOW_MESSAGE
            else:
                payload = bytes(connection.readAll())
        except RuntimeError:  # the socket was deleted between signal and slot
            return
        if payload.strip() not in {_SHOW_MESSAGE, b""}:
            return
        if self._window_ready:
            self.show_requested.emit()
        else:
            self._pending = True

    # ------------------------------------------------------------------
    def bind_window(self) -> None:
        """Mark the window as ready and flush a request that arrived early."""

        self._window_ready = True
        if self._pending:
            self._pending = False
            self.show_requested.emit()


_guard: SingleInstanceGuard | None = None


def acquire_single_instance() -> SingleInstanceGuard | None:
    """Return the guard when this process should run, ``None`` when it should exit.

    Registers the guard process-wide so the caller only has to keep the return
    value alive (returning it from ``main`` is enough).
    """

    global _guard
    if _guard is not None:
        return _guard
    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        return None
    _guard = guard
    return guard


__all__ = [
    "SingleInstanceGuard",
    "acquire_single_instance",
    "instance_key",
    "multiple_instances_allowed",
]
