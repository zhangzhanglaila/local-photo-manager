"""Instant splash screen that does not depend on Qt.

The Qt splash could only appear *after* ``import PySide6`` finished, which on a cold
boot (Defender scanning the DLLs) takes up to a minute — so the user stared at nothing.
This module draws a plain Win32 window through :mod:`ctypes` (standard library only),
which shows up in a few hundred milliseconds, and is closed once Qt is ready.

Everything degrades gracefully: if any call fails, :func:`show_native_splash` returns
``None`` and the caller falls back to the Qt splash.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Optional

_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_CS_HREDRAW = 0x0002
_CS_VREDRAW = 0x0001
_COLOR_WINDOW = 5
_DT_CENTER = 0x0001
_DT_SINGLELINE = 0x0020
_DT_VCENTER = 0x0004
_WM_PAINT = 0x000F
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_ERASEBKGND = 0x0014
_SW_SHOW = 5
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_FW_BOLD = 700

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WIDTH = 420
HEIGHT = 240


class NativeSplash:
    """Handle for the Win32 splash window; call :meth:`close` when Qt is ready."""

    def __init__(self) -> None:
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._wndproc = _WNDPROC(self._on_message)
        self._class_name = "iPhotoNativeSplash"

    # ------------------------------------------------------------------ public API
    def start(self) -> "NativeSplash":
        self._thread = threading.Thread(target=self._run, name="iPhoto-splash", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self

    def close(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            try:
                user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
            except OSError:
                pass

    def set_status(self, text: str) -> None:  # Qt parity; unused here
        del text

    def set_progress(self, percent: int, text: str | None = None) -> None:
        del percent, text

    # ------------------------------------------------------------------- internals
    def _run(self) -> None:
        try:
            self._create_window()
        except Exception:  # noqa: BLE001 - cosmetic only
            self._ready.set()
            self._hwnd = None
            return
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _create_window(self) -> None:
        instance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = _WNDCLASS()
        wc.style = _CS_HREDRAW | _CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = instance
        wc.hbrBackground = gdi32.CreateSolidBrush(0xFFFFFF)
        wc.lpszClassName = self._class_name
        user32.RegisterClassW(ctypes.byref(wc))

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = (screen_w - WIDTH) // 2
        y = (screen_h - HEIGHT) // 2

        self._hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW,
            self._class_name,
            "iPhoto",
            _WS_POPUP | _WS_VISIBLE,
            x,
            y,
            WIDTH,
            HEIGHT,
            None,
            None,
            instance,
            None,
        )
        if self._hwnd:
            user32.ShowWindow(self._hwnd, _SW_SHOW)
            user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE)

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == _WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == _WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == _WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(
            wintypes.HWND(hwnd),
            wintypes.UINT(msg),
            wintypes.WPARAM(wparam),
            wintypes.LPARAM(lparam),
        )

    def _paint(self, hwnd) -> None:
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            gdi32.SetBkMode(hdc, 1)  # transparent
            gdi32.SetTextColor(hdc, 0x2B2B2B)

            title_font = gdi32.CreateFontW(-30, 0, 0, 0, _FW_BOLD, 0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI")
            old = gdi32.SelectObject(hdc, title_font)
            rect = wintypes.RECT(0, 30, WIDTH, 110)
            user32.DrawTextW(hdc, "iPhoto", -1, ctypes.byref(rect), _DT_CENTER | _DT_SINGLELINE | _DT_VCENTER)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(title_font)

            body_font = gdi32.CreateFontW(-16, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI")
            old = gdi32.SelectObject(hdc, body_font)
            gdi32.SetTextColor(hdc, 0x7B5E3B)
            rect = wintypes.RECT(0, 115, WIDTH, 150)
            user32.DrawTextW(hdc, "本地照片库 · 正在启动…", -1, ctypes.byref(rect), _DT_CENTER | _DT_SINGLELINE | _DT_VCENTER)
            gdi32.SetTextColor(hdc, 0x9A948A)
            rect = wintypes.RECT(0, 185, WIDTH, 215)
            user32.DrawTextW(hdc, "数据仅在本机处理，不上传服务器", -1, ctypes.byref(rect), _DT_CENTER | _DT_SINGLELINE | _DT_VCENTER)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(body_font)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))


def show_native_splash() -> Optional[NativeSplash]:
    """Return a running native splash, or ``None`` when it is unavailable."""

    try:
        return NativeSplash().start()
    except Exception:  # noqa: BLE001 - fall back to the Qt splash
        return None
