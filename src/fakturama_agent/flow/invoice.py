"""Phase 4: the linked Invoice of the saved Order (brief 4.6-5.7).

Part B (this module so far): find the saved Order by its Cust.Ref. in Data >
Documents (exactly one, verified as ``check-order`` does), open its editor or
bind its already open tab, invoke the follow-up ``Invoice`` (never the toolbar's:
only the follow-up keeps the Order relationship), bind the new ``New Invoice``
editor by its window handle, and confirm what it copied from the Order (5.1).
The proposed No., Invoice Date and Service date are read, never typed. Nothing is
saved here.

The Invoice editor has the Order's layout (recorded 2026-11-06), so ``OrderEditor``
binds it and the Order's line, footer and address checks apply unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from fakturama_agent.flow.create_order import Step, verify_documents
from fakturama_agent.flow.order_header import OrderEditor
from fakturama_agent.flow.order_lines import footer_problems, invoice_tab_problems, lines_problems, read_footer
from fakturama_agent.flow.order_select import check_delivery, debtor_key, read_items
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia import actions
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.datefield import DateEntryError, display, parse_display, shows
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat
from fakturama_agent.uia.record_editor import EditorError, RecordEditor, read_view
from fakturama_agent.uia.tables import TableError
from fakturama_agent.uia.tree import snapshot_hwnd

NEW_INVOICE = "New Invoice"
INVOICE_NO = re.compile(r"^INV\d{3,}$")


class InvoiceError(RuntimeError):
    pass


# --- pure rules -------------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceHeader:
    number: str
    date: str
    service_date: str
    order_date: str
    cust_ref: str
    price_mode: str
    vat_mode: str


def header_problems(h: InvoiceHeader, spec: OrderSpec) -> list[str]:
    """Pure: the copied header (5.1). Dates proposed by Fakturama are only required to be dates."""
    p = []
    if not INVOICE_NO.match(h.number):
        p.append(f"No. is {h.number!r}, not a proposed Invoice number")
    for label, text in (("Invoice Date", h.date), ("Service date", h.service_date)):
        try:
            parse_display(text)  # Fakturama's proposal: only required to be a date
        except DateEntryError:
            p.append(f"{label} shows {text!r}, not a date")
    if not shows(h.order_date, spec.order.order_date):
        p.append(f"Order Date shows {h.order_date!r}, expected {display(spec.order.order_date)}")
    if h.cust_ref != spec.order.external_reference:
        p.append(f"Cust.Ref. is {h.cust_ref!r}")
    if h.price_mode != "Net":
        p.append(f"price mode is {h.price_mode!r}, expected Net")
    if h.vat_mode != "With VAT":
        p.append(f"VAT is {h.vat_mode!r}, expected With VAT")
    return p


# --- live ---------------------------------------------------------------------------------------


def _panes(main: MainWindow, name: str) -> list[int]:
    return [int(n.automation_id) for n in snapshot_hwnd(main.hwnd).root.walk()
            if n.control_type == "Pane" and n.name == name and n.automation_id]


def open_saved_order(main: MainWindow, registry: Registry, spec: OrderSpec, ocr: Callable) -> tuple[OrderEditor, str]:
    """The saved Order of this Cust.Ref. in an editor (opened, or its open tab bound)."""
    step = verify_documents(main, registry, spec, ocr)
    if not step.ok:
        raise InvoiceError(f"the saved Order is not verified: {step.detail}")
    number = step.detail.split(":", 1)[0]
    open_tabs = _panes(main, number)
    if len(open_tabs) > 1:
        raise InvoiceError(f"{len(open_tabs)} editors are titled {number!r}")
    if open_tabs:
        RecordEditor(main, registry, open_tabs[0], number).activate()
        return OrderEditor(main, registry, open_tabs[0]), number
    grid, table = read_view(main, registry, "documents", ocr)
    rows = [i for i, r in enumerate(table.rows) if r.get("Document") == number]
    if len(rows) != 1:
        raise InvoiceError(f"{len(rows)} Documents rows are {number!r}")
    editor = RecordEditor.open_row(main, registry, grid, table, rows[0], editor_name=number)
    return OrderEditor(main, registry, editor.hwnd), number


def open_linked_invoice(order: OrderEditor, *, timeout: float = 15.0) -> OrderEditor:
    """Invoke the Order's follow-up Invoice and bind the new 'New Invoice' editor."""
    main = order.main
    dialogs = [w.title for w in process_windows(main.pid) if w.hwnd != main.hwnd and w.title]
    if dialogs:
        raise InvoiceError(f"close these Fakturama dialogs first: {dialogs}")
    if _panes(main, NEW_INVOICE):
        raise InvoiceError("a 'New Invoice' editor is already open: close it first (it would be ambiguous)")
    button = order.node("order.followup.invoice")
    if not button.enabled:
        raise InvoiceError("the Order's follow-up Invoice is disabled (is the Order saved?)")
    actions.invoke(button)

    def bound() -> int | None:
        fresh = _panes(main, NEW_INVOICE)
        if len(fresh) > 1:
            raise InvoiceError(f"{len(fresh)} 'New Invoice' editors appeared")
        return fresh[0] if fresh else None

    hwnd = actions.wait_until(bound, timeout=timeout, what="the linked New Invoice editor")
    invoice = OrderEditor(main, order.registry, hwnd)
    actions.wait_until(lambda: True if invoice.node("order.no").value else None, timeout=timeout,
                       what="the New Invoice fields")
    return invoice


def read_invoice_header(invoice: OrderEditor) -> InvoiceHeader:
    snap = invoice.snapshot()

    def v(key: str) -> str:
        return invoice.registry.resolve(snap, key).value or ""

    return InvoiceHeader(v("order.no"), v("order.date"), v("invoice.service_date"), v("invoice.order_date"),
                         v("order.cust_ref"), v("order.price_mode"), v("order.vat_mode"))


def check_copy(invoice: OrderEditor, spec: OrderSpec, money: MoneyFormat, ocr: Callable) -> list[Step]:
    """5.1: header, addresses, lines and totals were copied from the Order. Read-only;
    ends with the Invoice address tab selected."""
    steps = []

    def add(name: str, problems: list[str], ok: str) -> bool:
        steps.append(Step(name, not problems, "; ".join(problems) if problems else ok))
        return not problems

    h = read_invoice_header(invoice)
    add("Invoice header", header_problems(h, spec),
        f"{h.number} (proposed), Date {h.date}, Service date {h.service_date} (kept); Order Date {h.order_date}, "
        f"{h.cust_ref}, {h.price_mode}, {h.vat_mode}")
    try:
        delivery = (check_delivery(invoice, spec)
                    if spec.delivery_address and not spec.derived.billing_equals_delivery else [])
    except TableError as e:
        delivery = [f"no delivery address on the Invoice ({e})"]
    add("Invoice addresses", delivery + invoice_tab_problems(invoice, debtor_key(spec)),
        "invoice and delivery address copied; Invoice address tab selected")
    try:
        rows = read_items(invoice, ocr)
        add("Invoice lines", lines_problems(rows, spec),
            ", ".join(f"{r.get('Item No.')} {r.get('Qty.')} = {r.get('Price')}" for r in rows))
    except TableError as e:
        add("Invoice lines", [f"cannot read the Invoice's items: {e}"], "")
    t = spec.derived.totals
    add("Invoice totals", footer_problems(read_footer(invoice), spec, money),
        f"net {t.net}, VAT {t.vat}, total {t.gross}; discount 0%, free shipping")
    return steps


@dataclass
class InvoiceRun:
    steps: list[Step]
    order_number: str | None = None
    invoice: OrderEditor | None = None  # the bound, still unsaved Invoice editor

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


def open_invoice(main: MainWindow, registry: Registry, spec: OrderSpec, money: MoneyFormat,
                 ocr: Callable) -> InvoiceRun:
    """Parts B (and later C): open the linked Invoice and confirm it; never saves."""
    run = InvoiceRun([])
    try:
        order, number = open_saved_order(main, registry, spec, ocr)
        run.order_number = number
        run.steps.append(Step("source Order", True, f"{number} found once by {spec.order.external_reference!r}"))
        run.invoice = open_linked_invoice(order)
        run.steps.append(Step("linked Invoice", True, f"follow-up Invoice of {number} opened (unsaved)"))
        run.steps += check_copy(run.invoice, spec, money, ocr)
    except (InvoiceError, EditorError, TableError, actions.ActionError, TimeoutError) as e:
        run.steps.append(Step("linked Invoice", False, str(e)))
    return run
