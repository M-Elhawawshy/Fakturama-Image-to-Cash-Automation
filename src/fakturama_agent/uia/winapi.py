"""Thin Win32 layer (ctypes): enumerate top-level windows and identify processes.

Window discovery uses EnumWindows rather than a UIA desktop walk, which can
hang on an unrelated application. Everything here is read-only.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import PureWindowsPath


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    class_name: str
    title: str
    visible: bool
    minimized: bool
    exe_name: str  # e.g. "Fakturama.exe"; "" if the process cannot be queried


def _require_windows() -> None:
    if sys.platform != "win32":
        raise OSError("Windows is required for UI automation")


def _user32():
    _require_windows()
    from ctypes import wintypes as w

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.IsWindowVisible.argtypes = [w.HWND]
    u.IsIconic.argtypes = [w.HWND]
    u.GetForegroundWindow.restype = w.HWND
    return u


def process_image_path(pid: int) -> str:
    """Full path of a process's executable ("" if it cannot be queried)."""
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = w.HANDLE
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    k.CloseHandle.argtypes = [w.HANDLE]
    process_query_limited_information = 0x1000
    handle = k.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ""
    try:
        size = w.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k.CloseHandle(handle)


def _exe_name(pid: int, cache: dict[int, str]) -> str:
    if pid not in cache:
        path = process_image_path(pid)
        cache[pid] = PureWindowsPath(path).name if path else ""
    return cache[pid]


def enum_windows() -> list[WindowInfo]:
    """All top-level windows of the desktop, in Z order."""
    from ctypes import wintypes as w

    u = _user32()
    found: list[WindowInfo] = []
    exe_cache: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def callback(hwnd, _lparam):
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(1024)
        u.GetWindowTextW(hwnd, title, 1024)
        found.append(
            WindowInfo(
                hwnd=int(hwnd or 0),
                pid=int(pid.value),
                class_name=cls.value,
                title=title.value,
                visible=bool(u.IsWindowVisible(hwnd)),
                minimized=bool(u.IsIconic(hwnd)),
                exe_name=_exe_name(int(pid.value), exe_cache),
            )
        )
        return True

    if not u.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def foreground_hwnd() -> int:
    return int(_user32().GetForegroundWindow() or 0)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Screen rectangle (left, top, right, bottom) of a top-level window."""
    from ctypes import byref, wintypes

    rect = wintypes.RECT()
    if not _user32().GetWindowRect(hwnd, byref(rect)):
        raise OSError(f"GetWindowRect failed for {hwnd}")
    return rect.left, rect.top, rect.right, rect.bottom
