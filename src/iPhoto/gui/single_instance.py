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
import time
from pathlib import Path

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
        self._mutex: int | None = None
        self._lock_file = None
        #: A request that arrived before the window existed.
        self._pending = False
        self._window_ready = False

    # ------------------------------------------------------------------
    @property
    def key(self) -> str:
        return self._key

    def try_acquire(self) -> bool:
        """Return ``True`` when this process is the one that should keep running.

        The election is an OS-level lock, not a "can I connect to the socket?"
        test: three quick launches used to race each other, because none of them
        had started listening yet, and every one of them concluded it was first.
        A named mutex (Windows) or an exclusive file lock (elsewhere) is settled
        atomically by the kernel, so exactly one process wins.
        """

        if multiple_instances_allowed():
            _LOGGER.info("single-instance guard disabled by IPHOTO_ALLOW_MULTIPLE")
            return True

        if not self._acquire_os_lock():
            _LOGGER.info("another iPhoto instance owns the lock; asking it to show itself")
            self._request_show_with_retry()
            return False

        self._start_server()
        return True

    # ------------------------------------------------------------------
    def _acquire_os_lock(self) -> bool:
        if os.name == "nt":
            return self._acquire_windows_mutex()
        return self._acquire_posix_lock()

    def _acquire_windows_mutex(self) -> bool:
        """Take the named mutex; the kernel releases it when we exit."""

        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, True, self._key)
            if not handle:
                _LOGGER.warning(
                    "could not create the single-instance mutex (error %s)",
                    ctypes.get_last_error(),
                )
                return True  # never block startup on the guard
            already_exists = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
            if already_exists:
                kernel32.CloseHandle(handle)
                return False
            self._mutex = handle
            return True
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("single-instance mutex unavailable", exc_info=True)
            return True

    def _acquire_posix_lock(self) -> bool:
        import tempfile

        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows without msvcrt fallback
            return True

        path = Path(tempfile.gettempdir()) / f"{self._key}.lock"
        try:
            handle = path.open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        except Exception:  # pragma: no cover - defensive
            return True
        self._lock_file = handle
        return True

    # ------------------------------------------------------------------
    def release(self) -> None:
        """Drop the lock and stop listening (tests and deliberate hand-over)."""

        if self._server is not None:
            self._server.close()
            self._server = None
        if self._mutex is not None:
            try:
                import ctypes

                ctypes.WinDLL("kernel32").CloseHandle(self._mutex)
            except Exception:  # pragma: no cover - defensive
                pass
            self._mutex = None
        if self._lock_file is not None:
            try:
                self._lock_file.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self._lock_file = None

    # ------------------------------------------------------------------
    def _start_server(self) -> None:
        """Listen for "show" requests; failure only costs the focus, not the guard."""

        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._accept_connection)
        if not server.listen(self._key):
            # The mutex already guarantees a single instance, so a busy pipe here
            # just means an older process still owns the name.
            QLocalServer.removeServer(self._key)
            if not server.listen(self._key):
                _LOGGER.warning(
                    "could not listen on %s (%s); the window will not be raised "
                    "from a second launch",
                    self._key,
                    server.errorString(),
                )
                return
        self._server = server
        _LOGGER.info("single-instance guard listening on %s", self._key)

    def _request_show_with_retry(self, attempts: int = 20, delay_ms: int = 150) -> bool:
        """Ask the running instance to show itself, waiting for it to listen.

        A launch that happens while the first instance is still starting has to
        retry: the owner may not have created its server yet.
        """

        for attempt in range(attempts):
            if self._notify_running_instance():
                return True
            if attempt + 1 < attempts:
                time.sleep(delay_ms / 1000.0)
        return False

    def _notify_running_instance(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(400):
            return False
        socket.write(_SHOW_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        _LOGGER.info("asked the running instance to show itself")
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
