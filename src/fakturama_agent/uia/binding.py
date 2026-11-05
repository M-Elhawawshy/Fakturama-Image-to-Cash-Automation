"""Bind Fakturama's main window, and the process's other windows (dialogs).

Fakturama keeps several SWT_Window0 windows (hidden helpers such as
"SWT_Window_Fakturama" and "PartRenderingEngine's limbo"). The main window is
the one that is visible, owned by Fakturama.exe, of class SWT_Window0, and
titled "Fakturama" or "Fakturama - <workspace path>". Exactly one must exist.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fakturama_agent.uia.winapi import WindowInfo, enum_windows

MAIN_CLASS = "SWT_Window0"
EXE_NAME = "fakturama.exe"
_MAIN_TITLE = re.compile(r"^Fakturama(?: - (?P<workspace>.+))?$")


class BindError(RuntimeError):
    """Fakturama could not be bound unambiguously (operational, not a review case)."""


@dataclass(frozen=True)
class MainWindow:
    hwnd: int
    pid: int
    title: str
    workspace: str | None
    minimized: bool


def _is_main(w: WindowInfo) -> bool:
    return (
        w.visible
        and w.class_name == MAIN_CLASS
        and w.exe_name.lower() == EXE_NAME
        and _MAIN_TITLE.match(w.title) is not None
    )


def find_main_window(windows: Iterable[WindowInfo] | None = None) -> MainWindow:
    all_windows = list(enum_windows() if windows is None else windows)
    candidates = [w for w in all_windows if _is_main(w)]
    if not candidates:
        running = sorted({w.pid for w in all_windows if w.exe_name.lower() == EXE_NAME})
        if running:
            raise BindError(f"Fakturama is running (pid {running}) but has no visible main window")
        raise BindError("Fakturama is not running; start it and open the workspace first")
    if len(candidates) > 1:
        listing = ", ".join(f"pid {w.pid} '{w.title}'" for w in candidates)
        raise BindError(f"{len(candidates)} Fakturama main windows found ({listing}); keep exactly one open")
    w = candidates[0]
    workspace = _MAIN_TITLE.match(w.title)["workspace"]
    return MainWindow(hwnd=w.hwnd, pid=w.pid, title=w.title, workspace=workspace, minimized=w.minimized)


def process_windows(
    pid: int, windows: Iterable[WindowInfo] | None = None, *, visible_only: bool = True
) -> list[WindowInfo]:
    """Top-level windows of the Fakturama process (the main window and any top-level dialogs)."""
    source: Callable[[], list[WindowInfo]] = enum_windows
    items = list(source() if windows is None else windows)
    return [w for w in items if w.pid == pid and (w.visible or not visible_only)]
