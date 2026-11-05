"""Part D: open a New Order, set its header, read it back. Never saves.

Binding: the editor is the Pane named "New Order" that appears after invoking
``Create: New Order`` and whose native window handle did not exist before. That
handle is the identity of "our" editor from then on (tab titles repeat, and a
dirty tab is renamed "*New Order"). Every read snapshots that handle only, and
every action first checks it is still the visible editor.

Header (DESIGN.md step 2; findings): Cust.Ref. = external_reference, price
mode Net, VAT With VAT, Date = order_date. The proposed No. is kept, only read.
"""

from __future__ import annotations

import ctypes
import re
import time
from dataclasses import asdict, dataclass
from datetime import date

from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.datefield import DateEntryError, display, enter_date, parse_display, shows
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.tree import Snapshot, UiNode, snapshot_hwnd

EDITOR_NAME = "New Order"
PRICE_MODE = "Net"
VAT_MODE = "With VAT"
_DOC_NO = re.compile(r"^[A-Z]{1,5}\d{3,}$")


class HeaderError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeaderWanted:
    cust_ref: str
    order_date: date
    price_mode: str = PRICE_MODE
    vat_mode: str = VAT_MODE


@dataclass(frozen=True)
class HeaderRead:
    number: str
    date_shown: str
    cust_ref: str
    price_mode: str
    vat_mode: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Check:
    field: str
    expected: str
    seen: str
    ok: bool


def compare(wanted: HeaderWanted, read: HeaderRead) -> list[Check]:
    """Pure: what was read back against what was wanted."""
    return [
        Check("No.", "proposed number kept", read.number, bool(_DOC_NO.match(read.number))),
        Check("Date", display(wanted.order_date), read.date_shown, shows(read.date_shown, wanted.order_date)),
        Check("Cust.Ref.", wanted.cust_ref, read.cust_ref, read.cust_ref == wanted.cust_ref),
        Check("Net/Gross", wanted.price_mode, read.price_mode, read.price_mode == wanted.price_mode),
        Check("VAT", wanted.vat_mode, read.vat_mode, read.vat_mode == wanted.vat_mode),
    ]


def read_header(editor: Snapshot | UiNode, registry: Registry) -> HeaderRead:
    def value(key: str) -> str:
        return registry.resolve(editor, key).value or ""

    return HeaderRead(
        number=value("order.no"),
        date_shown=value("order.date"),
        cust_ref=value("order.cust_ref"),
        price_mode=value("order.price_mode"),
        vat_mode=value("order.vat_mode"),
    )


# --------------------------------------------------------------------------
# live editor
# --------------------------------------------------------------------------


def _editor_panes(snap: Snapshot) -> list[UiNode]:
    return [n for n in snap.root.walk() if n.control_type == "Pane" and n.name == EDITOR_NAME and n.automation_id]


class OrderEditor:
    def __init__(self, main: MainWindow, registry: Registry, hwnd: int) -> None:
        self.main = main
        self.registry = registry
        self.hwnd = hwnd

    @classmethod
    def open_new(cls, main: MainWindow, registry: Registry, *, timeout: float = 15.0) -> OrderEditor:
        dialogs = [w.title for w in process_windows(main.pid) if w.hwnd != main.hwnd and w.title]
        if dialogs:
            raise HeaderError(f"close these Fakturama dialogs first: {dialogs}")
        if not actions.bring_to_front(main.hwnd):
            raise HeaderError("Fakturama could not be brought to the front")
        snap = snapshot_hwnd(main.hwnd)
        before = {n.automation_id for n in _editor_panes(snap)}
        actions.invoke(registry.resolve(snap, "toolbar.new_order"))

        def new_pane() -> int | None:
            fresh = [n for n in _editor_panes(snapshot_hwnd(main.hwnd)) if n.automation_id not in before]
            if len(fresh) > 1:
                raise HeaderError(f"{len(fresh)} new '{EDITOR_NAME}' editors appeared")
            return int(fresh[0].automation_id) if fresh else None

        hwnd = actions.wait_until(new_pane, timeout=timeout, what="the New Order editor")
        editor = cls(main, registry, hwnd)
        actions.wait_until(lambda: True if editor._ready() else None, timeout=timeout, what="the New Order fields")
        return editor

    def _ready(self) -> bool:
        snap = self.snapshot()
        return bool(read_header(snap, self.registry).number)

    def snapshot(self) -> Snapshot:
        if not ctypes.windll.user32.IsWindowVisible(self.hwnd):
            raise HeaderError("the bound New Order editor is not visible (closed, or another tab is in front)")
        return snapshot_hwnd(self.hwnd)

    def node(self, key: str) -> UiNode:
        return self.registry.resolve(self.snapshot(), key)

    def read(self) -> HeaderRead:
        return read_header(self.snapshot(), self.registry)

    # -- writes (unsaved editor only) ------------------------------------------

    def _blur(self) -> None:
        inputs.focus(self.node("order.consultant"), self.main.hwnd)
        time.sleep(0.3)

    def set_cust_ref(self, text: str) -> None:
        inputs.type_text(self.node("order.cust_ref"), text, self.main.hwnd)
        self._blur()

    def set_dropdown(self, key: str, item: str) -> None:
        inputs.choose(self.node(key), item, self.main.hwnd, refresh=lambda: self.node(key))

    def set_date(self, target: date) -> None:
        field = self.node("order.date")
        inputs.focus(field, self.main.hwnd)
        element = field.element.element
        widget = _LiveDate(lambda: element.GetCurrentPropertyValue(30045) or "")
        enter_date(widget, target)
        self._blur()
        shown = self.node("order.date").value or ""
        if not shows(shown, target):
            raise DateEntryError(f"Date shows {shown!r} after leaving the field, wanted {display(target)}")

    def set_header(self, wanted: HeaderWanted) -> HeaderRead:
        before = self.read()
        parse_display(before.date_shown)  # fails early if the field format is not the recorded one
        self.set_cust_ref(wanted.cust_ref)
        self.set_dropdown("order.price_mode", wanted.price_mode)
        self.set_dropdown("order.vat_mode", wanted.vat_mode)
        self.set_date(wanted.order_date)
        after = self.read()
        if after.number != before.number:
            raise HeaderError(f"the proposed No. changed from {before.number} to {after.number}")
        return after


class _LiveDate:
    def __init__(self, read) -> None:
        self._read = read

    def read(self) -> str:
        return self._read()

    def key(self, keys: str) -> None:
        inputs.send(keys)
        time.sleep(0.15)
