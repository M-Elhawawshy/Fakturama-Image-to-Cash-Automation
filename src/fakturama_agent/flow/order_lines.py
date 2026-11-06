"""Part I: the Order's lines, its verification, Save once, and the Documents row.

Recorded live (2026-11-05, unsaved Order with the sample's Debtor and products):
- a double-click on an item cell opens an inline UIA Edit exactly over the cell
  (Qty. shows '1.00', U.Price 'USD 40', Discount '0.00 %'); real keys replace the
  text, Enter commits. After Enter the Qty. editor moves on to the next row (from
  the last row back to the first); Escape closes that one unchanged. A plain
  number is accepted for every column ('2', '41.50', '10' for 10 %);
- the copy then holds model values: Qty. '2.00', U.Price 'USD 250' (currency of
  the app's locale, not the order's), Discount '-0.1' for 10 %, Price 'USD 450';
- the totals are display money ('$570.00', the preflight's MoneyFormat).
Each cell is located from the grid's own pixels (header separators, row bands).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from PIL import ImageGrab

from fakturama_agent.flow.match import DebtorKey, address_shows
from fakturama_agent.flow.order_header import OrderEditor
from fakturama_agent.flow.order_select import read_items
from fakturama_agent.spec.models import Item, OrderSpec
from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.grid import GridUnreadable, analyze, cell_point, column_edges
from fakturama_agent.uia.money import MoneyFormat, MoneyFormatError
from fakturama_agent.uia.tree import Snapshot, UiNode, visible_rect

ORDER_STATE_OPEN = "COMMAND_ORDER_PENDING"  # Documents shows 'open'
FREE_SHIPPING = "Free of shipping costs"
CENT = Decimal("0.01")


class LineError(RuntimeError):
    pass


# --- pure rules ------------------------------------------------------------------------------


def model_amount(text: str | None) -> Decimal | None:
    """'USD 450' / '678.3' / '11.899999999999999' -> Decimal rounded to cents; None if not a number."""
    raw = re.sub(r"^[A-Z]{3}\s+", "", (text or "").strip())
    try:
        return Decimal(raw).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _decimal(text: str | None) -> Decimal | None:
    try:
        return Decimal((text or "").strip())
    except InvalidOperation:
        return None


def plain(value: Decimal) -> str:
    """What is typed into a cell: '2', '10', '250.00' -> no exponent, no grouping."""
    return f"{value.normalize():f}"


def model_discount(percent: Decimal) -> Decimal:
    """Fakturama stores a 10 % line discount as -0.1."""
    return -(percent / 100)


def line_edits(row: dict, item: Item) -> list[tuple[str, str]]:
    """Pure: which cells must be typed (column, text) for ``row`` to carry ``item``."""
    edits = []
    if _decimal(row.get("Qty.")) != item.quantity:
        edits.append(("Qty.", plain(item.quantity)))
    if model_amount(row.get("U.Price")) != item.unit_net_price.quantize(CENT):
        edits.append(("U.Price", f"{item.unit_net_price:.2f}"))
    if _decimal(row.get("Discount")) != model_discount(item.discount_percent):
        edits.append(("Discount", plain(item.discount_percent)))
    return edits


def line_problems(row: dict, item: Item, vat_name: str, expected_net: Decimal) -> list[str]:
    """Pure: does a copied item row carry the spec's line exactly?"""
    p = []
    if row.get("Item No.") != item.sku:
        p.append(f"Item No. is {row.get('Item No.')!r}")
    if _decimal(row.get("Qty.")) != item.quantity:
        p.append(f"Qty. is {row.get('Qty.')!r}, expected {item.quantity}")
    if model_amount(row.get("U.Price")) != item.unit_net_price.quantize(CENT):
        p.append(f"U.Price is {row.get('U.Price')!r}, expected {item.unit_net_price}")
    if _decimal(row.get("Discount")) != model_discount(item.discount_percent):
        p.append(f"Discount is {row.get('Discount')!r}, expected {item.discount_percent}%")
    if f"name: [{vat_name}]" not in (row.get("VAT") or ""):
        p.append(f"VAT is not {vat_name!r}")
    if model_amount(row.get("Price")) != expected_net:
        p.append(f"Price is {row.get('Price')!r}, expected {expected_net}")
    return [f"line {item.line_no} ({item.sku}): {x}" for x in p]


def lines_problems(rows: list[dict], spec: OrderSpec) -> list[str]:
    if [r.get("Item No.") for r in rows] != [i.sku for i in spec.items]:
        return [f"the Order's lines are {[r.get('Item No.') for r in rows]}, expected {[i.sku for i in spec.items]}"]
    problems = []
    for row, item, d in zip(rows, spec.items, spec.derived.items, strict=True):
        problems += line_problems(row, item, d.vat_name, d.expected_line_net)
    return problems


@dataclass(frozen=True)
class Footer:
    total_net: str
    vat: str
    total: str
    discount: str
    shipping: str
    shipping_amount: str


def footer_problems(f: Footer, spec: OrderSpec, money: MoneyFormat) -> list[str]:
    """Pure: overall discount 0, free shipping 0.00, totals equal the spec's derived totals."""
    def amount(text: str) -> Decimal | None:
        try:
            return money.parse(text)
        except MoneyFormatError:
            return None

    t = spec.derived.totals
    p = []
    for label, text, want in (("Total Net", f.total_net, t.net), ("VAT", f.vat, t.vat), ("Total", f.total, t.gross)):
        if amount(text) != want:
            p.append(f"{label} shows {text!r}, expected {want}")
    if f.discount.strip().rstrip("%").strip() not in ("0", "0.0", "0.00"):
        p.append(f"overall Discount shows {f.discount!r}, expected 0%")
    if f.shipping != FREE_SHIPPING or amount(f.shipping_amount) != 0:
        p.append(f"Shipping shows {f.shipping!r} {f.shipping_amount!r}, expected {FREE_SHIPPING!r} 0.00")
    return p


def java_date(text: str | None) -> date | None:
    """'Tue Jul 14 00:00:00 EEST 2026' (the copy's Date) -> 2026-07-14."""
    m = re.fullmatch(r"\w{3} (\w{3}) (\d{1,2}) [\d:]{8} \S+ (\d{4})", (text or "").strip())
    if not m:
        return None
    return datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%b %d %Y").date()


def documents_problems(rows: list[dict], number: str, spec: OrderSpec, contact: str) -> list[str]:
    """Pure: Data > Documents (Orders) must hold exactly one row for the saved Order."""
    mine = [r for r in rows if r.get("Document") == number]
    if len(mine) != 1:
        return [f"{len(mine)} Documents rows are {number!r}"]
    r, p = mine[0], []
    ref = spec.order.external_reference
    if r.get("Cust.Ref.") != ref:
        p.append(f"Cust.Ref. is {r.get('Cust.Ref.')!r}")
    if sum(1 for x in rows if x.get("Cust.Ref.") == ref) != 1:
        p.append(f"{sum(1 for x in rows if x.get('Cust.Ref.') == ref)} orders carry Cust.Ref. {ref!r}")
    if java_date(r.get("Date")) != spec.order.order_date:
        p.append(f"Date is {r.get('Date')!r}, expected {spec.order.order_date}")
    if r.get("Name") != contact:
        p.append(f"Name is {r.get('Name')!r}, expected {contact!r}")
    if r.get("State") != ORDER_STATE_OPEN:
        p.append(f"State is {r.get('State')!r}, expected open ({ORDER_STATE_OPEN})")
    if model_amount(r.get("Total")) != spec.derived.totals.gross:
        p.append(f"Total is {r.get('Total')!r}, expected {spec.derived.totals.gross}")
    return [f"Documents {number}: {x}" for x in p]


def duplicates(rows: list[dict], cust_ref: str) -> list[str]:
    return [r.get("Document") or "?" for r in rows if r.get("Cust.Ref.") == cust_ref]


# --- live: the item grid ----------------------------------------------------------------------


def _grid_holder(root: UiNode) -> UiNode:
    labels = [n for n in root.walk() if n.control_type == "Text" and n.name == "Items"]
    if len(labels) != 1 or labels[0].parent is None:
        raise LineError(f"found {len(labels)} 'Items' labels in the Order")
    holders = [n for n in labels[0].parent.siblings_after() if n.control_type == "Pane"]
    inner = holders[0].children if holders else []
    if len(inner) != 1 or inner[0].control_type != "Pane":
        raise LineError("the Order's item grid is not where it was recorded")
    return inner[0]


def inline_editors(snap: Snapshot) -> list[UiNode]:
    return [n for n in _grid_holder(snap.root).walk() if n.control_type == "Edit"]


def _close_inline(editor: OrderEditor) -> None:
    """Escape any inline editor still open (the one Enter moved to the next row)."""
    if inline_editors(editor.snapshot()):
        actions.press_escape(editor.main.hwnd)
        actions.wait_until(lambda: True if not inline_editors(editor.snapshot()) else None, timeout=5.0,
                           what="the inline cell editor to close")


def edit_cell(editor: OrderEditor, row: int, column: str, text: str) -> None:
    columns = editor.registry.table("order_items").columns
    if inline_editors(editor.snapshot()):
        raise LineError("an inline cell editor is already open")
    grid = _grid_holder(editor.snapshot().root)
    actions.park_mouse(editor.main.hwnd)
    try:
        image = ImageGrab.grab(bbox=visible_rect(grid), all_screens=True)
    except ValueError as e:
        raise LineError(str(e)) from e
    try:
        pixels = analyze(image)
        edges = column_edges(image, pixels)
    except GridUnreadable as e:
        raise LineError(f"item grid unreadable: {e}") from e
    if len(edges) != len(columns) + 1:  # + the 'Pos.' row header
        raise LineError(f"the item grid shows {len(edges)} header cells, expected {len(columns) + 1}")
    if row >= pixels.ink_rows:
        raise LineError(f"line {row + 1} is not visible in the item grid ({pixels.ink_rows} lines)")
    index = columns.index(column) + 1
    dx, dy = cell_point(edges, index, pixels, row)
    actions.guarded_click_at(grid, dx, dy, editor.main.hwnd, double=True)
    cell = actions.wait_until(lambda: (e := inline_editors(editor.snapshot())) and e[0] or None, timeout=5.0,
                              what=f"the {column} cell editor of line {row + 1}")
    x, y = grid.rect[0] + dx, grid.rect[1] + dy
    if not (cell.rect[0] <= x <= cell.rect[2] and cell.rect[1] <= y <= cell.rect[3]):
        actions.press_escape(editor.main.hwnd)
        raise LineError(f"the cell editor opened at {cell.rect}, not over line {row + 1} {column}")
    inputs.type_text(cell, text, editor.main.hwnd)
    inputs.send("{ENTER}")
    time.sleep(0.5)
    _close_inline(editor)


def set_lines(editor: OrderEditor, spec: OrderSpec, ocr: Callable) -> list[dict]:
    """Type what differs, then read the lines back (read_table: twice, identical)."""
    rows = read_items(editor, ocr)
    if [r.get("Item No.") for r in rows] != [i.sku for i in spec.items]:
        raise LineError(f"the Order's lines are {[r.get('Item No.') for r in rows]}, "
                        f"expected {[i.sku for i in spec.items]}")
    for index, (row, item) in enumerate(zip(rows, spec.items, strict=True)):
        for column, text in line_edits(row, item):
            edit_cell(editor, index, column, text)
    return read_items(editor, ocr)


def read_footer(editor: OrderEditor) -> Footer:
    snap = editor.snapshot()

    def v(key: str) -> str:
        return editor.registry.resolve(snap, key).value or ""

    return Footer(v("order.total_net"), v("order.vat_total"), v("order.total"), v("order.discount"),
                  v("order.shipping"), v("order.shipping_amount"))


def invoice_tab_problems(editor: OrderEditor, key: DebtorKey) -> list[str]:
    """Select the Invoice address tab (saving with Delivery selected crashes 2.2.0),
    verify it is the selected one and shows the billing address."""
    from fakturama_agent.flow.order_select import _address_tab_text

    text = _address_tab_text(editor, "Invoice address")
    selected = [n.name for n in editor.snapshot().root.walk()
                if n.control_type == "TabItem" and n.selected and n.name in ("Invoice address", "Delivery address")]
    problems = [] if selected == ["Invoice address"] else [f"selected address tab is {selected}"]
    return problems + address_shows(text, key)



def addresses_cleared(invoice_text: str | None) -> list[str]:
    """Pure: an empty invoice address after the Debtor was selected and verified means
    Fakturama cleared the Order's addresses (seen live 2026-11-05 when that Debtor had
    been deleted). Never saved; a person checks the Debtor."""
    if (invoice_text or "").strip():
        return []
    return ["the Order's invoice address is EMPTY: its addresses were cleared after the Debtor was "
            "selected (was the Debtor deleted or changed meanwhile? check Data > Debtors, then rerun)"]
