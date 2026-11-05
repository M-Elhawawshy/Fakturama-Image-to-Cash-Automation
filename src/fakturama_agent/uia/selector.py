"""Live selector dialogs ("Select the address", "Select a product") opened from an Order.

Recorded behaviour (2026-11-05):
- each dialog is its own top-level window; Search filters the grid as you type
  (any column, case-insensitive);
- the address grid selects one row at a time (Ctrl+A copies just one row), so
  rows are read by **walking**: a guarded click on the first row band, then
  Down; Ctrl+Home/Down move the selection, Down stops at the last row;
- after a search no row is selected, and Copy with nothing selected raises an
  "Internal Error" (copiedCells is null). So before every Copy the pixels must
  show exactly the expected row highlighted;
- OK takes the selected row; with the "take over item number" preference off
  (preflight) the product dialog stays open after a unique search hit.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from PIL import Image, ImageGrab

from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.grid import GridPixels, GridUnreadable, analyze, check_header, read_header
from fakturama_agent.uia.locators import Registry, TableSpec
from fakturama_agent.uia.tables import TableError, accept_copy, parse_tsv, require_selection, stable_capture
from fakturama_agent.uia.tree import Snapshot, UiNode, snapshot_hwnd
from fakturama_agent.uia.views import copy_selection, focus_grid, grid_of

MAX_WALK = 20  # more search hits than this: refine the search (review), never read partially
# Only the top of a dialog grid is captured: enough for MAX_WALK rows plus margin. Twice
# (2026-11-05) something drew over the bottom of the 900-px product grid (far below the
# hits) and the detector rightly refused the picture; that region is never needed.
CAPTURE_HEIGHT = 640


class SelectorError(RuntimeError):
    pass


def _windows_titled(pid: int, title: str) -> list[int]:
    return [w.hwnd for w in process_windows(pid) if w.title == title]


class SelectorDialog:
    def __init__(self, main: MainWindow, registry: Registry, spec: TableSpec, hwnd: int, ocr: Callable) -> None:
        self.main = main
        self.registry = registry
        self.spec = spec
        self.hwnd = hwnd
        self.ocr = ocr
        self.pixels: GridPixels | None = None

    @classmethod
    def open(cls, main: MainWindow, registry: Registry, icon: UiNode, table_key: str, ocr: Callable,
             *, timeout: float = 10.0) -> SelectorDialog:
        spec = registry.table(table_key)
        others = [w.title for w in process_windows(main.pid) if w.hwnd != main.hwnd and w.title]
        if others:
            raise SelectorError(f"close these Fakturama dialogs first: {others}")
        actions.guarded_click(icon, main.hwnd)

        def opened() -> int | None:
            found = _windows_titled(main.pid, spec.view)
            if len(found) > 1:
                raise SelectorError(f"{len(found)} '{spec.view}' dialogs are open")
            return found[0] if found else None

        hwnd = actions.wait_until(opened, timeout=timeout, what=f"the '{spec.view}' dialog")
        return cls(main, registry, spec, hwnd, ocr)

    # -- state -------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.hwnd in _windows_titled(self.main.pid, self.spec.view)

    def _snap(self) -> Snapshot:
        if not self.is_open():
            raise SelectorError(f"the '{self.spec.view}' dialog closed by itself")
        return snapshot_hwnd(self.hwnd)

    def _grid(self) -> UiNode:
        try:
            return grid_of(self._snap().root, self.registry)
        except TableError as e:
            raise SelectorError(str(e)) from e

    def capture(self) -> Image.Image:
        left, top, right, bottom = self._grid().rect
        return ImageGrab.grab(bbox=(left, top, right, min(bottom, top + CAPTURE_HEIGHT)), all_screens=True)

    # -- search and read -----------------------------------------------------------

    def search(self, text: str, *, settle: float = 0.8) -> None:
        inputs.type_text(self.registry.resolve(self._snap(), "selector.search"), text, self.hwnd)
        time.sleep(settle)
        if not self.is_open():
            raise SelectorError(
                f"'{self.spec.view}' closed itself after searching {text!r} (is 'take over a clearly found item"
                " number' on?); check the Order by hand"
            )
        shown = self.registry.resolve(self._snap(), "selector.search").value or ""
        if shown != text:
            raise SelectorError(f"the search field shows {shown!r}, typed {text!r}")

    def rows(self) -> list[dict[str, str | None]]:
        actions.park_mouse(self.hwnd)
        image = stable_capture(self)
        try:
            pixels = analyze(image)
            check_header(read_header(image, pixels, self.ocr), self.spec.expected_header(),
                         prefix=self.spec.checked is not None)
        except GridUnreadable as e:
            raise SelectorError(f"{self.spec.view}: {e}") from e
        self.pixels = pixels
        if pixels.empty:
            return []  # nothing selected or copyable: never Copy here
        if pixels.full or pixels.ink_rows > MAX_WALK:
            raise SelectorError(f"{pixels.ink_rows}+ search hits fill the dialog; too many to read reliably")
        return [self._copy_row(i) for i in range(pixels.ink_rows)]

    def _highlight(self, i: int) -> None:
        """Make row ``i`` the selected row: click for the first, Down for each next (as walked)."""
        grid = self._grid()
        if i == 0:
            dx, dy = self.pixels.click_point(0)
            actions.guarded_click_at(grid, dx, dy, self.hwnd)
        else:
            focus_grid(grid, self.hwnd)
            inputs.send("{DOWN}")
        time.sleep(0.25)
        require_selection(stable_capture(self), [i])

    def _copy_row(self, i: int) -> dict[str, str | None]:
        self._highlight(i)
        text = accept_copy(copy_selection(self._grid(), self.hwnd), self.main.pid, f"row {i + 1}")
        rows = parse_tsv(text, self.spec)
        if len(rows) != 1:
            raise SelectorError(f"copying row {i + 1} gave {len(rows)} rows")
        return rows[0]

    # -- finish -----------------------------------------------------------------------

    def select(self, index: int, expected: dict[str, str | None], *, timeout: float = 5.0) -> None:
        """Select row ``index`` (walking from the top), verify it is ``expected`` by copy, press OK."""
        for i in range(index + 1):
            self._highlight(i)
        text = accept_copy(copy_selection(self._grid(), self.hwnd), self.main.pid, "selected row")
        got = parse_tsv(text, self.spec)
        if got != [expected]:
            raise SelectorError(f"the selected row is {got}, expected {expected}; not pressing OK")
        actions.invoke(self.registry.resolve(self._snap(), "selector.ok"))
        self._wait_closed(timeout)

    def cancel(self, *, timeout: float = 5.0) -> None:
        if self.is_open():
            actions.invoke(self.registry.resolve(self._snap(), "selector.cancel"))
            self._wait_closed(timeout)

    def _wait_closed(self, timeout: float) -> None:
        actions.wait_until(lambda: True if not self.is_open() else None, timeout=timeout,
                           what=f"the '{self.spec.view}' dialog to close")


def search_term(text: str) -> str:
    """The search box filters by substring; the whole value is typed (never a prefix)."""
    if not text or re.search(r"[\r\n\t]", text):
        raise SelectorError(f"cannot search for {text!r}")
    return text
