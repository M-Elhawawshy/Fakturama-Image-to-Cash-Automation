"""Field validation marks: Fakturama flags a rejected value with a small red mark
at the top-left corner of the field (recorded 2026-11-05 on the Debtor E-Mail:
'marta.klein@example.test' -> mark with tooltip 'Please enter a valid e-mail
address'; 'marta.klein@example.com' -> no mark). UIA exposes nothing for it, so
it is read from the pixels just left of the field's top-left corner.
"""

from __future__ import annotations

from PIL import Image, ImageGrab

from fakturama_agent.uia.tree import UiNode

BOX = (-12, -4, 2, 10)  # relative to the field's (left, top): left, top, right, bottom
MIN_RED = 5  # the recorded mark has 23 red pixels, a clean field none


def _red(p: tuple[int, ...]) -> bool:
    r, g, b = p[:3]
    return r > 150 and g < 110 and b < 110


def has_error_mark(image: Image.Image) -> bool:
    """Pure: does the captured corner box hold the red validation mark?"""
    rgb = image.convert("RGB")
    count = sum(1 for x in range(rgb.width) for y in range(rgb.height) if _red(rgb.getpixel((x, y))))
    return count >= MIN_RED


def mark_box(node: UiNode) -> tuple[int, int, int, int]:
    left, top = node.rect[0], node.rect[1]
    return left + BOX[0], top + BOX[1], left + BOX[2], top + BOX[3]


def field_marked(node: UiNode) -> bool:
    """Live: capture the corner box of a visible field (park the mouse first: a tooltip could cover it)."""
    return has_error_mark(ImageGrab.grab(bbox=mark_box(node), all_screens=True))
