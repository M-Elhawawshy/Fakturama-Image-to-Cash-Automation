"""Live UIA actions on nodes from a live snapshot, plus a condition wait.

Kept minimal; part D adds typing with read-back and dropdown selection.
Actions go through UIA patterns. The one exception is ``guarded_click``: a real
click at the centre of a control's UIA rectangle, only after Fakturama is in
front and a UIA hit test confirms that exact control is under the point. There
are no fixed coordinates.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from fakturama_agent.uia.tree import UiNode

T = TypeVar("T")


class ActionError(RuntimeError):
    pass


def _wrapper(node: UiNode):
    if node.element is None:
        raise ActionError(f"{node.label()} comes from a recorded tree, not the live application")
    from pywinauto.controls.uiawrapper import UIAWrapper

    return UIAWrapper(node.element)


def invoke(node: UiNode) -> None:
    if "Invoke" not in node.patterns:
        raise ActionError(f"{node.label()} has no Invoke pattern")
    _wrapper(node).invoke()


def bring_to_front(hwnd: int) -> bool:
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    from fakturama_agent.uia.winapi import foreground_hwnd

    if foreground_hwnd() == hwnd:
        return True
    try:
        UIAWrapper(UIAElementInfo(hwnd)).set_focus()
    except Exception:  # noqa: BLE001 - the foreground check below decides
        pass
    time.sleep(0.3)
    return foreground_hwnd() == hwnd


def _element_at(x: int, y: int):
    from ctypes import wintypes

    from pywinauto.uia_defines import IUIA

    return IUIA().iuia.ElementFromPoint(wintypes.POINT(x, y))


def _same_element(a, b) -> bool:
    from pywinauto.uia_defines import IUIA

    return bool(IUIA().iuia.CompareElements(a, b))


def guarded_click(node: UiNode, owner_hwnd: int) -> None:
    """Real left click at the centre of ``node``'s UIA rectangle, with guards."""
    w = _wrapper(node)
    left, top, right, bottom = node.rect
    if right <= left or bottom <= top:
        raise ActionError(f"{node.label()} has an empty rectangle {node.rect}")
    if not bring_to_front(owner_hwnd):
        raise ActionError("Fakturama could not be brought to the front; refusing to click")
    x, y = node.center
    if not _same_element(_element_at(x, y), node.element.element):
        raise ActionError(f"{node.label()} is not the element at {x},{y} (covered or moved); refusing to click")
    w.click_input()


def guarded_click_at(node: UiNode, dx: int, dy: int, owner_hwnd: int) -> None:
    """Real left click at (dx, dy) inside ``node``'s UIA rectangle (e.g. a row band of a
    NatTable grid, whose rows are not UIA elements), with the same guards."""
    from pywinauto import mouse

    left, top, right, bottom = node.rect
    x, y = left + dx, top + dy
    if not (left <= x < right and top <= y < bottom):
        raise ActionError(f"point {dx},{dy} is outside {node.label()} {node.rect}")
    if node.element is None:
        raise ActionError(f"{node.label()} comes from a recorded tree, not the live application")
    if not bring_to_front(owner_hwnd):
        raise ActionError("the window could not be brought to the front; refusing to click")
    if not _same_element(_element_at(x, y), node.element.element):
        raise ActionError(f"{node.label()} is not the element at {x},{y} (covered or moved); refusing to click")
    mouse.click(coords=(x, y))


def park_mouse(owner_hwnd: int) -> None:
    """Move the pointer onto the window's title bar, so no hover tooltip or highlight
    lies over a grid that is about to be captured (seen live: the Items icon's
    tooltip covered the Order's item grid right after the icon was clicked)."""
    import ctypes
    from ctypes import wintypes

    from pywinauto import mouse

    rect = wintypes.RECT()
    if ctypes.windll.user32.GetWindowRect(owner_hwnd, ctypes.byref(rect)):
        mouse.move(coords=((rect.left + rect.right) // 2, rect.top + 10))
        time.sleep(0.4)  # let a tooltip fade


def press_escape(owner_hwnd: int) -> None:
    """Escape to Fakturama only (closes an open menu); never sent to another window."""
    from pywinauto.keyboard import send_keys

    from fakturama_agent.uia.winapi import foreground_hwnd

    if foreground_hwnd() == owner_hwnd:
        send_keys("{ESC}")


def open_menu(node: UiNode, owner_hwnd: int) -> str:
    """Open a top-level menu; returns the method that was accepted.

    Seen live (2026-11-05): Invoke, Expand and the accessible default action can all
    be refused with COM -2146233079 while the item is enabled and collapsed; a real
    click opened it every time. So: the UIA methods first, then a guarded click.
    The caller must wait for the menu to appear; acceptance is not proof.
    """
    w = _wrapper(node)
    errors = []
    attempts = []
    if "Invoke" in node.patterns:
        attempts.append(("invoke", w.invoke))
    attempts.append(("expand", w.expand))

    def default_action() -> None:
        from pywinauto.uia_defines import get_elem_interface

        get_elem_interface(node.element.element, "LegacyIAccessible").DoDefaultAction()

    attempts.append(("default action", default_action))
    attempts.append(("click", lambda: guarded_click(node, owner_hwnd)))
    for name, attempt in attempts:
        try:
            attempt()
            return name
        except Exception as e:  # noqa: BLE001 - try the next method
            errors.append(f"{name}: {e}")
    raise ActionError(f"could not open {node.label()} ({'; '.join(errors)})")


def select(node: UiNode) -> None:
    if "SelectionItem" not in node.patterns:
        raise ActionError(f"{node.label()} has no SelectionItem pattern")
    _wrapper(node).select()


def wait_until(
    probe: Callable[[], T | None], *, timeout: float = 10.0, interval: float = 0.25, what: str = "condition"
) -> T:
    """Call ``probe`` until it returns something other than None (exceptions count as "not yet")."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while True:
        try:
            result = probe()
            if result is not None:
                return result
        except Exception as e:  # noqa: BLE001 - UIA trees are in flux while windows open/close
            last_error = e
        if time.monotonic() >= deadline:
            detail = f"; last error: {last_error}" if last_error else ""
            raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}{detail}")
        time.sleep(interval)
