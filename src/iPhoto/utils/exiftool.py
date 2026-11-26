"""Helpers for invoking the :command:`exiftool` CLI."""

from __future__ import annotations

import atexit
import collections
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..errors import ExternalToolError

LOGGER = logging.getLogger(__name__)

_EXIFTOOL_ENV_VAR = "IPHOTO_EXIFTOOL_PATH"
_MACOS_EXIFTOOL_CANDIDATES = (
    Path("/opt/homebrew/bin/exiftool"),
    Path("/usr/local/bin/exiftool"),
    Path("/opt/local/bin/exiftool"),
)

# Common Windows locations where ExifTool may be installed (via Chocolatey,
# Scoop, or manually).  Also include the miniconda/perl wrapper path that
# many users rely on.
_WINDOWS_EXIFTOOL_CANDIDATES: list[Path] = []


def _build_windows_candidates() -> list[Path]:
    """Lazily build the list of Windows ExifTool candidate paths."""
    if _WINDOWS_EXIFTOOL_CANDIDATES:
        return _WINDOWS_EXIFTOOL_CANDIDATES

    candidates: list[Path] = []

    # Project-local wrapper (exiftool.bat in repo root)
    project_root = Path(__file__).resolve().parents[3]  # src/iPhoto/utils → repo root
    project_bat = project_root / "exiftool.bat"
    if project_bat.is_file():
        candidates.append(project_bat)

    # Miniconda / Anaconda installations
    for base_dir in (
        Path.home() / "miniconda3",
        Path.home() / "anaconda3",
        Path("D:/programs/miniconda3"),
        Path("D:/programs/anaconda3"),
        Path("C:/ProgramData/miniconda3"),
        Path("C:/ProgramData/anaconda3"),
    ):
        candidate = base_dir / "bin" / "exiftool"
        if candidate.is_file():
            candidates.append(candidate)
        # Also check the Scripts directory
        candidate = base_dir / "Scripts" / "exiftool.exe"
        if candidate.is_file():
            candidates.append(candidate)

    # Chocolatey
    choco_base = Path("C:/ProgramData/chocolatey/bin")
    candidate = choco_base / "exiftool.exe"
    if candidate.is_file():
        candidates.append(candidate)

    # Scoop
    scoop_base = Path.home() / "scoop" / "shims"
    candidate = scoop_base / "exiftool.exe"
    if candidate.is_file():
        candidates.append(candidate)

    # Common manual install locations
    for program_dir in (
        Path("C:/Program Files/exiftool"),
        Path("C:/Program Files (x86)/exiftool"),
        Path.home() / "exiftool",
    ):
        candidate = program_dir / "exiftool.exe"
        if candidate.is_file():
            candidates.append(candidate)

    _WINDOWS_EXIFTOOL_CANDIDATES.extend(candidates)
    return _WINDOWS_EXIFTOOL_CANDIDATES


def _windows_executable_suffixes() -> tuple[str, ...]:
    raw_value = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    suffixes = tuple(
        entry.strip().lower()
        for entry in raw_value.split(os.pathsep)
        if entry.strip()
    )
    if suffixes:
        return suffixes
    return (".com", ".exe", ".bat", ".cmd")


def _is_executable_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        if os.name == "nt":
            return path.suffix.lower() in _windows_executable_suffixes()
        return os.access(path, os.X_OK)
    except OSError:
        return False


def _resolve_exiftool_executable() -> str:
    configured = os.environ.get(_EXIFTOOL_ENV_VAR, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if _is_executable_file(candidate):
            return str(candidate)
        raise ExternalToolError(
            f"Configured exiftool executable is not available or executable: {candidate}. "
            f"Set {_EXIFTOOL_ENV_VAR} to a valid executable path."
        )

    executable = shutil.which("exiftool")
    if executable:
        return executable

    searched = ["PATH"]
    if sys.platform == "darwin":
        for candidate in _MACOS_EXIFTOOL_CANDIDATES:
            searched.append(str(candidate))
            if _is_executable_file(candidate):
                return str(candidate)
    elif sys.platform == "win32":
        for candidate in _build_windows_candidates():
            searched.append(str(candidate))
            if _is_executable_file(candidate):
                LOGGER.info("Auto-detected exiftool at %s", candidate)
                return str(candidate)

    raise ExternalToolError(
        "exiftool executable not found. Install it from https://exiftool.org/ "
        f"and ensure it is available on PATH, or set {_EXIFTOOL_ENV_VAR}. "
        f"Searched: {', '.join(searched)}."
    )


def argv_from_wrapper(executable: str) -> list[str] | None:
    """Return the argv a ``.bat``/``.cmd`` wrapper would run, if it is simple.

    On Windows ``shutil.which("exiftool")`` finds the project's ``exiftool.bat``,
    so every session used to start ``cmd.exe`` *and* ``perl.exe``. Only ``cmd.exe``
    is the direct child, so killing it left ``perl.exe`` behind — that is how the
    machine accumulated dozens of idle ExifTool processes. Running the interpreter
    directly removes the wrapper from the chain entirely.

    Returns ``None`` when the batch does something more elaborate than
    ``perl.exe -I<lib> <exiftool-script> %*``.
    """

    path = Path(executable)
    if path.suffix.lower() not in {".bat", ".cmd"}:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    import re

    for line in text.splitlines():
        lowered = line.lower()
        if "perl" not in lowered or "exiftool" not in lowered:
            continue
        if lowered.lstrip().startswith(("@rem", "rem ", "echo", "@echo", "::")):
            continue
        tokens = [
            token.replace('"', "")
            for token in re.findall(r'"[^"]*"|\S+', line)
        ]
        tokens = [token for token in tokens if token]
        tokens = [token for token in tokens if not token.startswith("%")]
        if len(tokens) < 2 or "perl" not in tokens[0].lower():
            continue
        if not Path(tokens[0]).is_file():
            continue
        if not Path(tokens[-1]).is_file():
            continue
        return tokens
    return None


def launch_argv(executable: str) -> list[str]:
    """Return the argv that starts ExifTool without a shell wrapper."""

    if os.name == "nt":
        direct = argv_from_wrapper(executable)
        if direct is not None:
            return direct
    return [executable]


def _kill_tree(process: "subprocess.Popen[str]") -> None:
    """Kill *process* and any children it spawned."""

    pid = getattr(process, "pid", None)
    if pid is None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass


_job_handle: int | None = None
_job_attempted = False


def _kill_on_close_job() -> int | None:
    """Create (once) a Windows job object that kills children when we exit."""

    global _job_handle, _job_attempted
    if os.name != "nt":
        return None
    if _job_handle is not None or _job_attempted:
        return _job_handle
    _job_attempted = True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimits()
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(job)
            return None
        _job_handle = int(job)
    except Exception:  # pragma: no cover - job objects are best effort
        LOGGER.debug("could not create the ExifTool job object", exc_info=True)
        _job_handle = None
    return _job_handle


def _adopt(process: "subprocess.Popen[str]") -> None:
    """Put *process* in the kill-on-close job so it cannot outlive the app."""

    job = _kill_on_close_job()
    if job is None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = int(getattr(process, "_handle", 0) or 0)
        if not handle:
            return
        if not kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job), wintypes.HANDLE(handle)
        ):
            LOGGER.debug(
                "could not put ExifTool in the job object (error %s)",
                ctypes.get_last_error(),
            )
    except Exception:  # pragma: no cover - best effort
        LOGGER.debug("could not adopt the ExifTool process", exc_info=True)


def _startup_options() -> tuple[subprocess.STARTUPINFO | None, int]:
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def _safe_exiftool_path(path: Path) -> str:
    safe_path = path.absolute()
    path_str = safe_path.as_posix()
    if "\n" in path_str or "\r" in path_str:
        raise ExternalToolError(f"Unsafe newline characters in path: {path_str!r}")
    return path_str


def _run_exiftool_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    startupinfo, creationflags = _startup_options()
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise ExternalToolError(f"Failed to execute exiftool (FileNotFoundError): {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown error"
        raise ExternalToolError(f"ExifTool failed with an error: {stderr}") from exc


def _format_iso6709(latitude: float, longitude: float) -> str:
    return f"{latitude:+.6f}{longitude:+.6f}/"


# ---------------------------------------------------------------------------
# Persistent ExifTool session
#
# ExifTool costs ~410 ms per launch (Perl start-up plus module loading), and it
# was launched once per batch — and once per info-panel open. ``-stay_open`` keeps
# the interpreter alive and turns subsequent requests into ~20 ms.
#
# The session is an optimisation only: every failure falls back to the one-shot
# path so behaviour never depends on it.
# ---------------------------------------------------------------------------

_STAY_OPEN_ENV = "IPHOTO_EXIFTOOL_STAY_OPEN"
_SESSION_ARGS = ("-n", "-g1", "-json", "-charset", "filename=utf8")
_READY_MARKER = "{ready}"
_SESSION_MIN_TIMEOUT_S = 5.0
#: Seconds allowed per requested file on top of the base timeout. HEIC and video
#: files cost several times more than JPEGs; the previous fixed budget timed out
#: on healthy 200-file batches and paid a needless session restart.
_SESSION_TIMEOUT_PER_FILE_S = 0.15
_STDERR_LINES = 50

_session: "_ExifToolSession | None" = None
_session_executable: str | None = None
_session_lock = threading.Lock()


class _ExifToolSession:
    """A long-lived ``exiftool -stay_open True`` process.

    Wire protocol: arguments and paths are written to stdin, terminated by
    ``-execute``; ExifTool answers with the JSON payload followed by ``{ready}``.
    A reader thread feeds a queue so the caller can wait with a timeout — pipes
    on Windows do not support ``select``.
    """

    def __init__(self, executable: str) -> None:
        startupinfo, creationflags = _startup_options()
        self._argv = launch_argv(executable) + ["-stay_open", "True", "-@", "-"]
        self._process = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        _adopt(self._process)
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=_STDERR_LINES)
        self._closed = False
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, name="exiftool-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="exiftool-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    # -- readers ---------------------------------------------------------
    def _drain_stdout(self) -> None:
        stream = self._process.stdout
        if stream is None:  # pragma: no cover - defensive
            return
        try:
            for line in stream:
                self._lines.put(line)
        except (OSError, ValueError):  # pragma: no cover - process teardown
            pass
        finally:
            self._lines.put(None)

    def _drain_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:  # pragma: no cover - defensive
            return
        try:
            for line in stream:
                self._stderr.append(line.rstrip())
        except (OSError, ValueError):  # pragma: no cover - process teardown
            pass

    # -- requests --------------------------------------------------------
    def read(self, paths: list[Path], timeout: float) -> str:
        """Run one ``-execute`` cycle and return the raw stdout payload."""

        if self._closed:
            raise ExternalToolError("ExifTool session is closed")
        stdin = self._process.stdin
        if stdin is None:  # pragma: no cover - defensive
            raise ExternalToolError("ExifTool session has no stdin")

        arguments = list(_SESSION_ARGS)
        for path in paths:
            arguments.append(_safe_exiftool_path(path))
        arguments.append("-execute")

        try:
            stdin.write("\n".join(arguments) + "\n")
            stdin.flush()
        except (OSError, ValueError) as exc:
            raise ExternalToolError(f"Failed to talk to ExifTool session: {exc}") from exc

        deadline = time.monotonic() + timeout
        collected: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExternalToolError(
                    f"ExifTool session timed out after {timeout:.0f}s"
                )
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise ExternalToolError(
                    f"ExifTool session timed out after {timeout:.0f}s"
                ) from exc
            if line is None:
                detail = "; ".join(self._stderr) or "no stderr output"
                raise ExternalToolError(f"ExifTool session exited unexpectedly: {detail}")
            if line.strip() == _READY_MARKER:
                return "".join(collected)
            collected.append(line)

    # -- lifecycle -------------------------------------------------------
    def is_alive(self) -> bool:
        return not self._closed and self._process.poll() is None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            stdin = self._process.stdin
            if stdin is not None:
                stdin.write("-stay_open\nFalse\n")
                stdin.flush()
                stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - stuck process
            # ``kill`` only reaches the direct child; the Perl interpreter sits one
            # level below the old ``cmd.exe`` wrapper, so kill the whole tree.
            _kill_tree(self._process)
        for thread in (self._stdout_thread, self._stderr_thread):
            thread.join(timeout=2)


def release_metadata_handles() -> None:
    """Close the persistent session so Windows releases any open file handles.

    File operations call this when a move/delete fails with "file in use": a
    background metadata read may still hold the file open. The next read restarts
    the session, so the cost is one start-up (~0.5 s) on a rare path.
    """

    _close_session()


def _stay_open_enabled() -> bool:
    raw = os.environ.get(_STAY_OPEN_ENV, "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _session_for(executable: str) -> _ExifToolSession | None:
    """Return the shared session, starting it on first use.

    Returns ``None`` when the session cannot be used, so callers fall back to the
    one-shot path instead of failing.
    """

    global _session, _session_executable

    if not _stay_open_enabled():
        return None
    if _session is not None and _session_executable == executable and _session.is_alive():
        return _session
    if _session is not None:
        _session.close()
        _session = None
        _session_executable = None
    try:
        _session = _ExifToolSession(executable)
    except (OSError, ValueError) as exc:
        LOGGER.warning("Could not start persistent ExifTool session: %s", exc)
        _session = None
        return None
    _session_executable = executable
    LOGGER.debug("Started persistent ExifTool session")
    return _session


def _close_session_locked() -> None:
    """Close the shared session; the caller must hold `_session_lock`."""

    global _session, _session_executable

    if _session is not None:
        _session.close()
    _session = None
    _session_executable = None


def _close_session() -> None:
    """Shut the shared session down (used by tests and interpreter exit)."""

    with _session_lock:
        _close_session_locked()


def _read_metadata_persistent(
    paths: list[Path], executable: str
) -> list[dict[str, Any]] | None:
    """Read metadata through the shared session, or ``None`` to fall back."""

    with _session_lock:
        session = _session_for(executable)
        if session is None:
            return None
        # Budget from batch size: healthy batches finish well inside this, while a
        # genuinely hung process is still detected in reasonable time.
        timeout = _SESSION_MIN_TIMEOUT_S + _SESSION_TIMEOUT_PER_FILE_S * len(paths)
        try:
            payload = session.read(paths, timeout)
        except ExternalToolError as exc:
            LOGGER.warning("Persistent ExifTool session failed, retrying one-shot: %s", exc)
            _close_session_locked()
            return None
    if not payload.strip():
        return []
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExternalToolError(f"Failed to parse JSON output from ExifTool: {exc}") from exc


def _read_metadata_one_shot(paths: list[Path], executable: str) -> list[dict[str, Any]]:
    """Read metadata by launching one ``exiftool`` process (original path)."""

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp_arg_file:
        for path in paths:
            try:
                tmp_arg_file.write(_safe_exiftool_path(path) + "\n")
            except ExternalToolError:
                LOGGER.warning("Skipping file with unsafe characters in path: %s", path)
                continue
        tmp_arg_path = tmp_arg_file.name

    try:
        cmd = [
            *launch_argv(executable),
            *_SESSION_ARGS,
            "-@",
            tmp_arg_path,
        ]
        try:
            process = _run_exiftool_command(cmd)
            return json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise ExternalToolError(f"Failed to parse JSON output from ExifTool: {exc}") from exc
    except ExternalToolError as exc:
        message = str(exc)
        if "image files read" in message.lower():
            return []
        raise
    finally:
        try:
            os.remove(tmp_arg_path)
        except OSError:
            pass


def get_metadata_batch(paths: list[Path]) -> list[dict[str, Any]]:
    """Return metadata for *paths*, reusing a persistent ExifTool process.

    ExifTool's start-up cost dominates small requests, so the process is kept
    alive between calls; the original one-shot implementation is still used as a
    fallback whenever the persistent session is unavailable or fails.
    """

    executable = _resolve_exiftool_executable()
    if not paths:
        return []

    persistent = _read_metadata_persistent(paths, executable)
    if persistent is not None:
        return persistent
    return _read_metadata_one_shot(paths, executable)



def write_gps_metadata(
    path: Path,
    *,
    latitude: float,
    longitude: float,
    is_video: bool,
) -> None:
    """Write GPS metadata in-place without generating a sidecar file."""

    executable = _resolve_exiftool_executable()
    safe_path = _safe_exiftool_path(path)
    cmd = [
        *launch_argv(executable),
        "-overwrite_original",
        "-charset",
        "filename=utf8",
    ]
    if is_video:
        iso6709 = _format_iso6709(latitude, longitude)
        cmd.append(f"-GPSCoordinates={iso6709}")
    else:
        cmd.extend(
            [
                f"-GPSLatitude={abs(float(latitude)):.8f}",
                f"-GPSLatitudeRef={'S' if latitude < 0 else 'N'}",
                f"-GPSLongitude={abs(float(longitude)):.8f}",
                f"-GPSLongitudeRef={'W' if longitude < 0 else 'E'}",
            ]
        )
    cmd.append(safe_path)
    _run_exiftool_command(cmd)


atexit.register(_close_session)


__all__ = [
    "argv_from_wrapper",
    "get_metadata_batch",
    "launch_argv",
    "release_metadata_handles",
    "write_gps_metadata",
]
