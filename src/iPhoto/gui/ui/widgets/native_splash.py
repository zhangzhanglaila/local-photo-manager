"""Instant splash screen drawn with Win32 (ctypes only).

The Qt splash could only appear after ``import PySide6`` finished, which on a cold boot
takes up to a minute, so the window stayed invisible while the app loaded. This module
paints the same design (icon, product name, brown accent, thin progress bar) using plain
GDI calls, and animates the bar from its own message loop so it never freezes.

If anything fails, :func:`show_native_splash` returns ``None`` and the caller falls back
to the Qt splash.
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Optional

_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_CS_HREDRAW = 0x0002
_CS_VREDRAW = 0x0001
_DT_LEFT = 0x0000
_DT_SINGLELINE = 0x0020
_DT_VCENTER = 0x0004
_DT_NOPREFIX = 0x0800
_WM_PAINT = 0x000F
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_TIMER = 0x0113
_SW_SHOW = 5
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_LR_LOADFROMFILE = 0x0010
_LR_DEFAULTSIZE = 0x0040
_IMAGE_ICON = 1
_DI_NORMAL = 0x0003
_TRANSPARENT = 1
_PS_SOLID = 0
_TIMER_ID = 1
_TIMER_MS = 33

WIDTH = 460
HEIGHT = 300


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


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH

gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPCWSTR,
]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.RoundRect.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int,
]
user32.DrawTextW.argtypes = [
    wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT,
]
user32.DrawIconEx.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int,
    wintypes.UINT, wintypes.HBRUSH, wintypes.UINT,
]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _icon_path() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates += [Path(local) / "iPhoto" / "app.ico", Path(local) / "iPhoto" / "app.png"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class NativeSplash:
    """Handle for the Win32 splash window; call :meth:`close` once Qt is ready."""

    def __init__(self) -> None:
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._wndproc = _WNDPROC(self._on_message)
        self._class_name = "iPhotoNativeSplash"
        self._hicon = None
        self._phase = 0

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

    def set_status(self, text: str) -> None:  # parity with the Qt splash
        del text

    def set_progress(self, percent: int, text: str | None = None) -> None:
        del percent, text

    # ------------------------------------------------------------------- internals
    def _run(self) -> None:
        try:
            self._create_window()
        except Exception:  # noqa: BLE001 - cosmetic only
            self._hwnd = None
            self._ready.set()
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
        wc.hbrBackground = gdi32.CreateSolidBrush(0xF8FAFA)  # matches the Qt card
        wc.lpszClassName = self._class_name
        user32.RegisterClassW(ctypes.byref(wc))

        icon_path = _icon_path()
        if icon_path is not None and icon_path.suffix.lower() == ".ico":
            self._hicon = user32.LoadImageW(
                None,
                str(icon_path),
                _IMAGE_ICON,
                0,
                0,
                _LR_LOADFROMFILE | _LR_DEFAULTSIZE,
            )

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        self._hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW,
            self._class_name,
            "iPhoto",
            _WS_POPUP | _WS_VISIBLE,
            (screen_w - WIDTH) // 2,
            (screen_h - HEIGHT) // 2,
            WIDTH,
            HEIGHT,
            None,
            None,
            instance,
            None,
        )
        if not self._hwnd:
            raise OSError("CreateWindowExW failed")
        user32.ShowWindow(self._hwnd, _SW_SHOW)
        user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE)
        user32.SetTimer(self._hwnd, _TIMER_ID, _TIMER_MS, None)

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == _WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == _WM_TIMER:
            self._phase = (self._phase + 1) % 40
            user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == _WM_CLOSE:
            user32.KillTimer(hwnd, _TIMER_ID)
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

    def _text(self, hdc, text: str, x: int, y: int, size: int, color: int, bold: bool = False) -> None:
        font = gdi32.CreateFontW(
            -size, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI"
        )
        old = gdi32.SelectObject(hdc, font)
        gdi32.SetTextColor(hdc, color)
        rect = wintypes.RECT(x, y, WIDTH - 36, y + size + 14)
        user32.DrawTextW(
            hdc, text, -1, ctypes.byref(rect),
            _DT_LEFT | _DT_SINGLELINE | _DT_VCENTER | _DT_NOPREFIX,
        )
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)

    def _paint(self, hwnd) -> None:
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            gdi32.SetBkMode(hdc, _TRANSPARENT)

            left = 36
            top = 34
            if self._hicon:
                user32.DrawIconEx(hdc, left, top, self._hicon, 56, 56, 0, None, _DI_NORMAL)
            top += 64

            self._text(hdc, "iPhoto", left, top, 30, 0x1F1F1F, bold=True)
            top += 44
            self._text(hdc, "本地照片库", left, top, 15, 0x3B5E7B, bold=True)
            top += 30
            self._text(hdc, "正在启动…", left, top, 13, 0x8C857C)

            # Thin progress bar: a rounded track with a segment sweeping across it.
            bar_y = HEIGHT - 58
            bar_h = 4
            track = gdi32.CreateSolidBrush(0xE9EFEF)
            old_brush = gdi32.SelectObject(hdc, track)
            gdi32.RoundRect(hdc, left, bar_y, WIDTH - left, bar_y + bar_h, 4, 4)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(track)

            span = WIDTH - 2 * left
            chunk = span // 4
            position = int((span - chunk) * (self._phase / 39.0))
            fill = gdi32.CreateSolidBrush(0x3B5E7B)
            old_brush = gdi32.SelectObject(hdc, fill)
            gdi32.RoundRect(hdc, left + position, bar_y, left + position + chunk, bar_y + bar_h, 4, 4)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(fill)

            self._text(hdc, "数据仅在本机处理，不上传服务器", left, HEIGHT - 40, 11, 0xA09A90)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))


def show_native_splash() -> Optional[NativeSplash]:
    """Return a running native splash, or ``None`` when it is unavailable."""

    try:
        return NativeSplash().start()
    except Exception:  # noqa: BLE001 - fall back to the Qt splash
        return None
