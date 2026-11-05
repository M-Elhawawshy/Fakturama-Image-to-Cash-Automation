"""Live backend for the table reader: show a Data view, find its grid, capture, copy.

Recorded structure (2026-11-05): the active view is a Pane named like the view
inside a Tab; it holds a search bar (Text "Search:" + Edit) and, after the
bar, an unnamed Pane: the NatTable grid. Documents additionally has a Tree of
categories left of the grid. View tabs refuse SelectionItem.Select and their
default action, so a tab is shown with a guarded click; a view without a tab is
opened by Invoke on Data > <view>.
"""

from __future__ import annotations

import time

from PIL import Image

from fakturama_agent.uia import actions, clipboard, inputs
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.clipboard import ClipboardText
from fakturama_agent.uia.locators import LocatorError, Registry, TableSpec
from fakturama_agent.uia.tables import TableError
from fakturama_agent.uia.tree import Snapshot, UiNode, snapshot_hwnd


def view_pane(snap: Snapshot, view: str) -> UiNode | None:
    panes = [n for n in snap.root.walk() if n.control_type == "Pane" and n.name == view]
    if len(panes) > 1:
        raise TableError(f"{len(panes)} panes named {view!r}")
    return panes[0] if panes else None


def grid_of(pane: UiNode, registry: Registry) -> UiNode:
    """The Pane right after the search bar (the bar is the parent of the label's parent)."""
    try:
        label = registry.resolve(pane, "view.search_label")
    except LocatorError as e:
        raise TableError(f"no search bar in view {pane.name!r} ({e.kind})") from e
    bar = label.parent.parent if label.parent and label.parent.parent else None
    following = [n for n in (bar.siblings_after() if bar else []) if n.control_type == "Pane"]
    if len(following) != 1 or following[0].children:
        raise TableError(f"view {pane.name!r}: expected one empty grid Pane after the search bar, found {len(following)}")
    grid = following[0]
    left, top, right, bottom = grid.rect
    if right - left < 50 or bottom - top < 50:
        raise TableError(f"view {pane.name!r}: grid rectangle {grid.rect} is too small to read")
    return grid


class LiveGrid:
    def __init__(self, main: MainWindow, registry: Registry, *, timeout: float = 10.0) -> None:
        self.main = main
        self.registry = registry
        self.timeout = timeout
        self.grid: UiNode | None = None

    # -- preconditions ------------------------------------------------------

    def _guard(self) -> None:
        dialogs = [w.title for w in process_windows(self.main.pid) if w.hwnd != self.main.hwnd and w.title]
        if dialogs:
            raise TableError(f"close these Fakturama dialogs first: {dialogs}")
        if not actions.bring_to_front(self.main.hwnd):
            raise TableError("Fakturama could not be brought to the front")

    def _snap(self) -> Snapshot:
        return snapshot_hwnd(self.main.hwnd)

    # -- show the view --------------------------------------------------------

    def show(self, spec: TableSpec) -> None:
        self._guard()
        snap = self._snap()
        tabs = [n for n in snap.root.walk() if n.control_type == "TabItem" and n.name == spec.view]
        if len(tabs) > 1:
            raise TableError(f"{len(tabs)} tabs named {spec.view!r}")
        if tabs:
            if not tabs[0].selected:
                actions.guarded_click(tabs[0], self.main.hwnd)
        else:
            self._open_from_data_menu(spec.view, snap)
        pane = actions.wait_until(lambda: view_pane(self._snap(), spec.view), timeout=self.timeout, what=f"view {spec.view!r}")
        if spec.filter:
            pane = self._select_filter(spec)
        actions.park_mouse(self.main.hwnd)
        self.grid = grid_of(pane, self.registry)

    def _open_from_data_menu(self, view: str, snap: Snapshot) -> None:
        data = self.registry.resolve(snap, "menu.data")
        actions.open_menu(data, self.main.hwnd)

        def item() -> UiNode | None:
            menu = self.registry.resolve(self._snap(), "menu.data.expanded")
            items = [n for n in menu.children if n.control_type == "MenuItem" and n.name == view]
            return items[0] if len(items) == 1 else None

        try:
            actions.invoke(actions.wait_until(item, timeout=self.timeout, what=f"Data > {view}"))
        except Exception:
            actions.press_escape(self.main.hwnd)
            raise

    def _select_filter(self, spec: TableSpec) -> UiNode:
        def tree_item(snap: Snapshot) -> UiNode:
            pane = view_pane(snap, spec.view)
            items = [n for n in pane.walk() if n.control_type == "TreeItem" and n.name == spec.filter] if pane else []
            if len(items) != 1:
                raise TableError(f"view {spec.view!r}: {len(items)} filter entries named {spec.filter!r}")
            return items[0]

        def selected() -> UiNode | None:
            snap = self._snap()
            return view_pane(snap, spec.view) if tree_item(snap).selected else None

        item = tree_item(self._snap())
        if item.selected:
            return actions.wait_until(selected, timeout=self.timeout, what=f"filter {spec.filter!r} selected")
        actions.select(item)
        try:
            return actions.wait_until(selected, timeout=2.0, what=f"filter {spec.filter!r} selected")
        except TimeoutError:
            # Seen after an Order Save refreshed the view: Select only focused the entry
            # (dotted outline, not selected). A real click on it selects it.
            actions.guarded_click(tree_item(self._snap()), self.main.hwnd)
        return actions.wait_until(selected, timeout=self.timeout, what=f"filter {spec.filter!r} selected")

    # -- capture and copy -------------------------------------------------------

    def capture(self) -> Image.Image:
        from PIL import ImageGrab

        assert self.grid is not None, "show() first"
        return ImageGrab.grab(bbox=self.grid.rect, all_screens=True)

    def select_all(self) -> None:
        assert self.grid is not None, "show() first"
        focus_grid(self.grid, self.main.hwnd)
        inputs.send("^a")
        time.sleep(0.2)

    def copy(self) -> ClipboardText:
        assert self.grid is not None, "show() first"
        return copy_selection(self.grid, self.main.hwnd)


def focus_grid(grid: UiNode, owner_hwnd: int) -> None:
    """UIA SetFocus on a grid Pane; verified by window handle (NatTable has no UIA children)."""
    from pywinauto.uia_defines import IUIA

    from fakturama_agent.uia.winapi import foreground_hwnd

    if foreground_hwnd() != owner_hwnd and not actions.bring_to_front(owner_hwnd):
        raise TableError("the window holding the grid could not be brought to the front")
    element = grid.element.element
    element.SetFocus()
    time.sleep(0.2)
    focused = IUIA().iuia.GetFocusedElement().CurrentNativeWindowHandle
    if focused != element.CurrentNativeWindowHandle or foreground_hwnd() != owner_hwnd:
        raise TableError("the grid did not get keyboard focus; not sending keys")


def copy_selection(grid: UiNode, owner_hwnd: int) -> ClipboardText:
    """Ctrl+C on the focused grid; returns once the clipboard sequence number changed.
    The caller must have checked that a selection is highlighted."""
    focus_grid(grid, owner_hwnd)
    before = clipboard.sequence_number()
    inputs.send("^c")
    actions.wait_until(
        lambda: True if clipboard.sequence_number() != before else None, timeout=3.0, what="the copy to reach the clipboard"
    )
    time.sleep(0.1)
    return clipboard.read_text()
