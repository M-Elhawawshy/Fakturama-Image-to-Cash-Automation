"""Input primitives with read-back: typing into an Edit, picking a dropdown entry.

Findings: UIA ValuePattern.SetValue and SelectionItem.Select can change the
displayed text without updating SWT's model, so text is typed with real key
events and dropdown entries are chosen by a guarded click on the exact
ListItem. Every write is read back after focus has left the control.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fakturama_agent.uia import actions
from fakturama_agent.uia.tree import UiNode

# characters pywinauto's send_keys treats as syntax
_SPECIAL = set("+^%~(){}[]")


class InputError(RuntimeError):
    pass


def escape_keys(text: str) -> str:
    """Literal text for pywinauto send_keys: syntax characters in braces."""
    if any(c in text for c in "\r\n\t"):
        raise InputError("line breaks and tabs are not typed into single-line fields")
    return "".join("{" + c + "}" if c in _SPECIAL else c for c in text)


VK_CONTROL = 0x11
KEYEVENTF_KEYUP = 0x0002


def ctrl_letter(keys: str) -> str | None:
    """Pure: the letter of a Ctrl+letter shortcut ('^a' -> 'A'), else None."""
    if len(keys) == 2 and keys[0] == "^" and keys[1].isascii() and keys[1].isalpha():
        return keys[1].upper()
    return None


def _send_ctrl_letter(letter: str) -> None:
    """Ctrl+letter as virtual-key codes ('A' = 0x41 ...), independent of the keyboard layout.

    Seen live (2026-11-06): with an Arabic (Egypt) layout active, pywinauto maps 'a'
    through VkKeyScan, which has no key for it, so '^a' / '^c' / '^w' never reached
    Fakturama (Select All silently did nothing)."""
    import ctypes

    user32 = ctypes.windll.user32
    vk = ord(letter)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)


def send(keys: str, *, literal: bool = False) -> None:
    from pywinauto.keyboard import send_keys

    if literal:
        send_keys(keys, with_spaces=True, pause=0.01)  # Unicode packets for text
    elif (letter := ctrl_letter(keys)) is not None:
        _send_ctrl_letter(letter)
    else:
        send_keys(keys, vk_packet=False)  # real virtual-key events (named keys, digits)


def focus(node: UiNode, owner_hwnd: int) -> None:
    """UIA SetFocus, then verify the focused element is that node and the owner is in front."""
    from pywinauto.uia_defines import IUIA

    from fakturama_agent.uia.winapi import foreground_hwnd

    if not actions.bring_to_front(owner_hwnd):
        raise InputError("Fakturama could not be brought to the front")
    element = node.element.element
    element.SetFocus()
    time.sleep(0.15)
    iuia = IUIA().iuia
    if not iuia.CompareElements(iuia.GetFocusedElement(), element) or foreground_hwnd() != owner_hwnd:
        raise InputError(f"{node.label()} did not get the keyboard focus")


def type_text(node: UiNode, text: str, owner_hwnd: int, *, keys: Callable[..., None] = send) -> None:
    """Replace an Edit's content with ``text`` using real key events (caller blurs and reads back)."""
    escaped = escape_keys(text)
    focus(node, owner_hwnd)
    keys("^a")
    keys("{BACKSPACE}")
    if text:
        keys(escaped, literal=True)


def list_items(combo: UiNode) -> list[UiNode]:
    """Entries of an expanded dropdown (they appear under the ComboBox while open)."""
    return [n for n in combo.walk() if n.control_type == "ListItem"]


def choose(
    combo: UiNode,
    item: str,
    owner_hwnd: int,
    refresh: Callable[[], UiNode],
    *,
    timeout: float = 5.0,
    strip: bool = False,
) -> None:
    """Pick ``item`` in a dropdown: expand, click the exact ListItem, verify the value.

    ``refresh`` returns a fresh live node for the same ComboBox. ``strip`` ignores
    leading/trailing spaces (payment codes are shown as 'Credit transfer ').
    The dropdown is only opened when the value must change: opening it and
    pressing Escape already marks an editor as changed (seen 2026-11-05).
    """
    same = (lambda a, b: (a or "").strip() == (b or "").strip()) if strip else (lambda a, b: a == b)
    if same(combo.value, item):
        return
    if not actions.bring_to_front(owner_hwnd):
        raise InputError("Fakturama could not be brought to the front")
    from pywinauto.controls.uiawrapper import UIAWrapper

    try:
        UIAWrapper(combo.element).expand()
    except Exception:  # noqa: BLE001 - fall back to the dropdown's own button
        opener = [n for n in combo.children if n.control_type == "Button" and n.name == "Open"]
        if len(opener) != 1:
            raise InputError(f"cannot open dropdown {combo.label()}") from None
        actions.guarded_click(opener[0], owner_hwnd)

    def exact_item() -> UiNode | None:
        found = [n for n in list_items(refresh()) if same(n.name, item)]
        if len(found) > 1:
            raise InputError(f"{len(found)} entries named {item!r} in {combo.label()}")
        return found[0] if found else None

    try:
        entry = actions.wait_until(exact_item, timeout=timeout, what=f"entry {item!r} in {combo.label()}")
        if entry.rect == (0, 0, 0, 0):  # scrolled out of a long list (Country): scroll it in first
            UIAWrapper(entry.element).iface_scroll_item.ScrollIntoView()
            entry = actions.wait_until(lambda: (e := exact_item()) and e.rect != (0, 0, 0, 0) and e or None,
                                       timeout=timeout, what=f"entry {item!r} scrolled into view")
        actions.guarded_click(entry, owner_hwnd)
        actions.wait_until(
            lambda: True if not list_items(refresh()) else None, timeout=timeout, what="the dropdown to close"
        )
    except Exception:
        actions.press_escape(owner_hwnd)
        raise
    value = refresh().value
    if not same(value, item):
        raise InputError(f"{combo.label()} shows {value!r} after choosing {item!r}")
