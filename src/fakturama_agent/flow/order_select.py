"""Part E: Debtor and products as existence checks from an open (unsaved) Order.

For the Debtor: open "Select the address" from the upper icon beside Addresses,
search the Company, read the hits, decide (flow.match). Select -> pick the
exact row (verified by copy), OK, then check the Order's invoice address.
Otherwise Cancel. Same for each item with "Select a product" and the full SKU;
a selection must add exactly one line with that Item No. to the Order's items.
Never saves; creating missing records is parts G/H.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image, ImageGrab

from fakturama_agent.flow.match import Decision, DebtorKey, address_shows, decide_debtor, decide_product, line_added
from fakturama_agent.flow.order_header import OrderEditor
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia import inputs
from fakturama_agent.uia.clipboard import ClipboardText
from fakturama_agent.uia.locators import TableSpec
from fakturama_agent.uia.selector import SelectorDialog, SelectorError, search_term
from fakturama_agent.uia.tables import TableError, read_table
from fakturama_agent.uia.tree import Snapshot, UiNode
from fakturama_agent.uia.views import copy_selection, focus_grid


@dataclass
class Outcome:
    what: str  # "Debtor" or "Item CHR-ERG-01"
    decision: Decision | None = None
    problems: list[str] = field(default_factory=list)  # verification after a selection
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.problems and self.decision is not None and not self.decision.needs_review

    def summary(self) -> str:
        if self.error:
            return f"STOPPED: {self.error}"
        d = self.decision
        text = {"select": "found and selected", "not_found": "not found (dialog cancelled)",
                "conflict": "CONFLICT, review (dialog cancelled)",
                "ambiguous": "AMBIGUOUS, review (dialog cancelled)"}[d.kind]
        verified = ""
        if d.kind == "select":
            verified = "; Order verified" if not self.problems else f"; ORDER CHECK FAILED: {self.problems}"
        return f"{text}: {d.reason}{verified}"


def debtor_key(spec: OrderSpec) -> DebtorKey:
    return DebtorKey(
        company=spec.debtor.company,
        first_name=spec.debtor.contact_first_name,
        last_name=spec.debtor.contact_last_name,
        zip=spec.billing_address.zip,
        city=spec.billing_address.city,
    )


# --- the Order's item grid --------------------------------------------------------


def items_grid(editor: Snapshot | UiNode) -> UiNode:
    """The NatTable right of the 'Items' label (recorded: Text 'Items' in a Pane, next Pane holds the grid)."""
    root = editor.root if isinstance(editor, Snapshot) else editor
    labels = [n for n in root.walk() if n.control_type == "Text" and n.name == "Items"]
    if len(labels) != 1 or labels[0].parent is None:
        raise TableError(f"found {len(labels)} 'Items' labels in the Order")
    holders = [n for n in labels[0].parent.siblings_after() if n.control_type == "Pane"]
    inner = holders[0].children if holders else []
    if len(inner) != 1 or inner[0].control_type != "Pane" or inner[0].children:
        raise TableError("the Order's item grid is not where it was recorded")
    return inner[0]


class ItemsGrid:
    """GridBackend for the bound Order's items (read with tables.read_table)."""

    def __init__(self, editor: OrderEditor) -> None:
        self.editor = editor
        self.grid: UiNode | None = None

    def show(self, spec: TableSpec) -> None:
        from fakturama_agent.uia.actions import park_mouse

        park_mouse(self.editor.main.hwnd)
        self.grid = items_grid(self.editor.snapshot())

    def capture(self) -> Image.Image:
        return ImageGrab.grab(bbox=self.grid.rect, all_screens=True)

    def select_all(self) -> None:
        focus_grid(self.grid, self.editor.main.hwnd)
        inputs.send("^a")
        time.sleep(0.2)

    def copy(self) -> ClipboardText:
        return copy_selection(self.grid, self.editor.main.hwnd)


def read_items(editor: OrderEditor, ocr: Callable) -> list[dict]:
    spec = editor.registry.table("order_items")
    return read_table("order_items", spec, ItemsGrid(editor), fakturama_pid=editor.main.pid, ocr=ocr).rows


# --- checks ---------------------------------------------------------------------------


def _run(dialog_opener: Callable[[], SelectorDialog], body: Callable[[SelectorDialog, Outcome], None],
         outcome: Outcome) -> Outcome:
    dialog = None
    try:
        dialog = dialog_opener()
        body(dialog, outcome)
    except (SelectorError, TableError, inputs.InputError, TimeoutError) as e:
        outcome.error = str(e)
    except Exception as e:  # noqa: BLE001 - reported as a stop, never retried
        outcome.error = f"{type(e).__name__}: {e}"
    finally:
        if dialog is not None and dialog.is_open():
            try:
                dialog.cancel()
            except Exception as e:  # noqa: BLE001
                outcome.error = (outcome.error or "") + f"; could not Cancel the dialog ({e}): close it by hand"
    return outcome


def check_debtor(editor: OrderEditor, spec: OrderSpec, ocr: Callable) -> Outcome:
    key = debtor_key(spec)

    def body(dialog: SelectorDialog, out: Outcome) -> None:
        dialog.search(search_term(key.company))
        rows = dialog.rows()
        out.decision = decide_debtor(rows, key)
        if out.decision.kind != "select":
            dialog.cancel()
            return
        dialog.select(out.decision.index, rows[out.decision.index])
        out.problems = address_shows(editor.node("order.invoice_address").value or "", key)

    opener = lambda: SelectorDialog.open(  # noqa: E731
        editor.main, editor.registry, editor.node("order.addresses.select_icon"), "select_address", ocr
    )
    return _run(opener, body, Outcome("Debtor"))


def check_product(editor: OrderEditor, sku: str, ocr: Callable) -> Outcome:
    def body(dialog: SelectorDialog, out: Outcome) -> None:
        dialog.search(search_term(sku))
        rows = dialog.rows()
        out.decision = decide_product(rows, sku)
        if out.decision.kind != "select":
            dialog.cancel()
            return
        dialog.select(out.decision.index, rows[out.decision.index])
        out.problems = line_added(before, read_items(editor, ocr), sku)

    before: list[dict] = []
    outcome = Outcome(f"Item {sku}")
    try:
        before = read_items(editor, ocr)
    except TableError as e:
        outcome.error = f"cannot read the Order's items first: {e}"
        return outcome
    opener = lambda: SelectorDialog.open(  # noqa: E731
        editor.main, editor.registry, editor.node("order.items.select_icon"), "select_product", ocr
    )
    return _run(opener, body, outcome)


def check_all(editor: OrderEditor, spec: OrderSpec, ocr: Callable) -> list[Outcome]:
    outcomes = [check_debtor(editor, spec, ocr)]
    for item in spec.items:
        if any(o.error for o in outcomes):
            break  # a stop leaves Fakturama in an unknown state: no further steps
        outcomes.append(check_product(editor, item.sku, ocr))
    return outcomes


def _address_tab_text(editor: OrderEditor, tab_name: str) -> str:
    """Select the Order's address tab ``tab_name`` and return its text."""
    from fakturama_agent.uia import actions

    def find(kind: str) -> list[UiNode]:
        return [n for n in editor.snapshot().root.walk() if n.control_type == kind and n.name == tab_name]

    items = find("TabItem")
    if len(items) != 1:
        raise TableError(f"{len(items)} '{tab_name}' tabs in the Order")
    if not items[0].selected:
        actions.guarded_click(items[0], editor.main.hwnd)
        actions.wait_until(lambda: True if find("TabItem")[0].selected else None, timeout=5.0,
                           what=f"'{tab_name}' selected")
        time.sleep(0.3)
    holders = find("Tab")  # the Tab control is named after its selected tab
    edits = [n for n in holders[0].walk() if n.control_type == "Edit"] if len(holders) == 1 else []
    if len(edits) != 1:
        raise TableError(f"cannot find the text of the Order's '{tab_name}' tab")
    return edits[0].value or ""


def check_delivery(editor: OrderEditor, spec: OrderSpec) -> list[str]:
    """After the Debtor was selected: the Delivery address tab shows the spec's delivery
    address. The Invoice address tab is selected again afterwards (saving with the
    Delivery tab selected crashes 2.2.0, R11) and that is verified."""
    from fakturama_agent.flow.match import delivery_shows

    a = spec.delivery_address
    try:
        text = _address_tab_text(editor, "Delivery address")
    finally:
        _address_tab_text(editor, "Invoice address")
    key = DebtorKey(spec.debtor.company, spec.debtor.contact_first_name, spec.debtor.contact_last_name, a.zip, a.city)
    return delivery_shows(text, key, a.street)
