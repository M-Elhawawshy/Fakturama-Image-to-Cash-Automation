"""Win32 clipboard: sequence number, owner process and Unicode text (read only)."""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

CF_UNICODETEXT = 13


class ClipboardError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClipboardText:
    sequence: int
    owner_pid: int
    text: str | None


def _apis():
    from ctypes import wintypes as w

    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.GetClipboardSequenceNumber.restype = w.DWORD
    u.OpenClipboard.argtypes = [w.HWND]
    u.GetClipboardOwner.restype = w.HWND
    u.GetClipboardData.argtypes = [w.UINT]
    u.GetClipboardData.restype = w.HANDLE
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    k.GlobalLock.argtypes = [w.HANDLE]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [w.HANDLE]
    return u, k, w


def sequence_number() -> int:
    u, _k, _w = _apis()
    return int(u.GetClipboardSequenceNumber())


def read_text(retries: int = 20) -> ClipboardText:
    u, k, w = _apis()
    for _ in range(retries):
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)  # another process holds it briefly
    else:
        raise ClipboardError("the clipboard is held by another program")
    try:
        sequence = int(u.GetClipboardSequenceNumber())
        pid = w.DWORD()
        owner = u.GetClipboardOwner()
        if owner:
            u.GetWindowThreadProcessId(owner, ctypes.byref(pid))
        handle = u.GetClipboardData(CF_UNICODETEXT)
        text = None
        if handle:
            ptr = k.GlobalLock(handle)
            try:
                text = ctypes.wstring_at(ptr) if ptr else None
            finally:
                k.GlobalUnlock(handle)
        return ClipboardText(sequence=sequence, owner_pid=int(pid.value), text=text)
    finally:
        u.CloseClipboard()
