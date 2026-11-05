"""Open the editor of one Data-view row, read it, close it unchanged.

Recorded (2026-11-05): double-clicking a row in Data > VATs / terms of payment
opens an editor tab named like the record (a Pane with its own window handle),
e.g. ``VAT 19%`` or ``Cash``. Ctrl+W on an unchanged editor closes it without
a prompt. A changed editor would ask to save: if any dialog appears, the
closing stops and the dialog is left for the user (never answered blindly).
"""

from __future__ import annotations

import ctypes
import time
from collections.abc import Callable

from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.tables import TableError, TableRead, read_table
from fakturama_agent.uia.tree import Snapshot, snapshot_hwnd
from fakturama_agent.uia.views import LiveGrid


class EditorError(RuntimeError):
    pass


def _named_panes(snap: Snapshot, name: str) -> set[str]:
    return {n.automation_id for n in snap.root.walk() if n.control_type == "Pane" and n.name == name and n.automation_id}


class RecordEditor:
    """A master-data editor bound by its window handle."""

    def __init__(self, main: MainWindow, registry: Registry, hwnd: int, name: str) -> None:
        self.main = main
        self.registry = registry
        self.hwnd = hwnd
        self.name = name

    @classmethod
    def open_row(cls, main: MainWindow, registry: Registry, grid: LiveGrid, read: TableRead, index: int,
                 *, timeout: float = 10.0) -> RecordEditor:
        name = read.rows[index]["Name"]
        if not name:
            raise EditorError(f"row {index + 1} has no Name")
        before = _named_panes(snapshot_hwnd(main.hwnd), name)
        dx, dy = read.pixels.click_point(index)
        actions.guarded_click_at(grid.grid, dx, dy, main.hwnd, double=True)

        def opened() -> int | None:
            fresh = _named_panes(snapshot_hwnd(main.hwnd), name) - before
            if len(fresh) > 1:
                raise EditorError(f"{len(fresh)} editors named {name!r} opened")
            return int(fresh.pop()) if fresh else None

        hwnd = actions.wait_until(opened, timeout=timeout, what=f"the editor of {name!r}")
        return cls(main, registry, hwnd, name)

    def snapshot(self) -> Snapshot:
        if not ctypes.windll.user32.IsWindowVisible(self.hwnd):
            raise EditorError(f"the {self.name!r} editor is not visible")
        return snapshot_hwnd(self.hwnd)

    def value(self, key: str) -> str:
        return self.registry.resolve(self.snapshot(), key).value or ""

    def _dirty(self) -> bool:
        tabs = [n.name for n in snapshot_hwnd(self.main.hwnd).root.walk() if n.control_type == "TabItem"]
        return f"*{self.name}" in tabs

    def activate(self) -> None:
        """Bring this editor's tab to the front (keys go to the active part only)."""
        tabs = [n for n in snapshot_hwnd(self.main.hwnd).root.walk()
                if n.control_type == "TabItem" and n.name in (self.name, f"*{self.name}")]
        if len(tabs) != 1:
            raise EditorError(f"{len(tabs)} tabs named {self.name!r}; cannot tell which is this editor")
        if not tabs[0].selected:
            actions.guarded_click(tabs[0], self.main.hwnd)
            actions.wait_until(lambda: True if ctypes.windll.user32.IsWindowVisible(self.hwnd) else None,
                               timeout=5.0, what=f"the {self.name!r} editor in front")

    def close_unchanged(self, focus_key: str, *, timeout: float = 5.0) -> None:
        if self._dirty():
            raise EditorError(f"the {self.name!r} editor shows unsaved changes; close it by hand and choose No")
        self.activate()
        inputs.focus(self.registry.resolve(self.snapshot(), focus_key), self.main.hwnd)
        inputs.send("^w")
        try:
            actions.wait_until(lambda: True if not ctypes.windll.user32.IsWindow(self.hwnd) else None,
                               timeout=timeout, what=f"the {self.name!r} editor to close")
        except TimeoutError:
            dialogs = [w.title for w in process_windows(self.main.pid) if w.hwnd != self.main.hwnd and w.title]
            raise EditorError(f"the {self.name!r} editor did not close (dialogs: {dialogs}); handle it by hand") from None
        time.sleep(0.2)


def read_view(main: MainWindow, registry: Registry, table_key: str, ocr: Callable) -> tuple[LiveGrid, TableRead]:
    grid = LiveGrid(main, registry)
    try:
        read = read_table(table_key, registry.table(table_key), grid, fakturama_pid=main.pid, ocr=ocr)
    except TableError as e:
        raise EditorError(str(e)) from e
    return grid, read
