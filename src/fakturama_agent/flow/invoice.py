"""Phase 4: the linked Invoice of the saved Order (brief 4.6-5.7).

Part B: find the saved Order by its Cust.Ref. in Data >
Documents (exactly one, verified as ``check-order`` does), open its editor or
bind its already open tab, invoke the follow-up ``Invoice`` (never the toolbar's:
only the follow-up keeps the Order relationship), bind the new ``New Invoice``
editor by its window handle, and confirm what it copied from the Order (5.1).
The proposed No., Invoice Date and Service date are read, never typed. Nothing is
saved here.

Part C: payment method and paid status (5.2/5.3), still unsaved: the method is
picked by the verified term's *description* (the dropdown lists descriptions);
PAID ticks ``paid``, enters the payment date in the ``at`` field and requires
``Value`` to equal the Invoice Total (typed only if it differs); anything else
leaves ``paid`` clear and touches no date or value.

The Invoice editor has the Order's layout (recorded 2026-11-06), so ``OrderEditor``
binds it and the Order's line, footer and address checks apply unchanged.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from fakturama_agent.flow.create_order import Step, verify_documents
from fakturama_agent.flow.master_data import check_payment
from fakturama_agent.flow.order_header import HeaderError, OrderEditor, _LiveDate
from fakturama_agent.flow.order_lines import (
    footer_problems,
    invoice_tab_problems,
    lines_problems,
    model_amount,
    read_footer,
)
from fakturama_agent.flow.order_select import check_delivery, debtor_key, read_items
from fakturama_agent.spec.models import OrderSpec, PaidStatus
from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.datefield import DateEntryError, display, enter_date, parse_display, shows
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat, MoneyFormatError
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


def open_saved_order(main: MainWindow, registry: Registry, spec: OrderSpec, ocr: Callable,
                     number: str | None = None, *, unique: bool = True) -> tuple[OrderEditor, str]:
    """The saved Order in an editor (opened, or its open tab bound): ``number`` (the run's own
    Order), else the one Order carrying the Cust.Ref."""
    step = verify_documents(main, registry, spec, ocr, number, unique=unique)
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


# --- part C: payment ------------------------------------------------------------------------------


def paid_problems(paid: str, shown_date: str, value: Decimal | None, total: Decimal | None,
                  spec: OrderSpec) -> list[str]:
    """Pure: the payment area read back against the spec (5.3)."""
    if spec.payment.paid_status != PaidStatus.PAID:
        return [] if paid == "off" else [f"paid is {paid!r}, but the status is {spec.payment.paid_status.value}"]
    p = []
    if paid != "on":
        p.append(f"paid is {paid!r}, expected ticked")
    if not shows(shown_date, spec.payment.payment_date):
        p.append(f"paid date shows {shown_date!r}, expected {display(spec.payment.payment_date)}")
    if total != spec.derived.totals.gross:
        p.append(f"Invoice Total is {total}, expected {spec.derived.totals.gross}")
    if value != total:
        p.append(f"Value is {value}, expected the full Invoice Total {total}")
    return p


def _money(money: MoneyFormat, text: str) -> Decimal | None:
    try:
        return money.parse(text)
    except MoneyFormatError:
        return None


def _blur(invoice: OrderEditor) -> None:
    inputs.focus(invoice.node("order.consultant"), invoice.main.hwnd)
    time.sleep(0.3)


def _enter_paid_date(invoice: OrderEditor, target) -> None:
    field = invoice.node("invoice.paid_date")
    inputs.focus(field, invoice.main.hwnd)
    element = field.element.element
    enter_date(_LiveDate(lambda: element.GetCurrentPropertyValue(30045) or ""), target)
    _blur(invoice)


def set_payment(invoice: OrderEditor, spec: OrderSpec, description: str, money: MoneyFormat) -> list[Step]:
    """5.2 and 5.3 in the unsaved Invoice; every value read back."""
    from pywinauto.controls.uiawrapper import UIAWrapper

    steps = []
    try:
        inputs.choose(invoice.node("invoice.payment"), description, invoice.main.hwnd,
                      refresh=lambda: invoice.node("invoice.payment"))
        shown = invoice.node("invoice.payment").value or ""
        method_problems = [] if shown == description else [f"payment shows {shown!r}, expected {description!r}"]
    except inputs.InputError as e:
        method_problems = [f"{spec.payment.method.value} ({description!r}) is not available: {e}"]
    steps.append(Step("Invoice payment method", not method_problems, "; ".join(method_problems) or
                      f"{description!r} ({spec.payment.method.value})"))
    if method_problems:
        return steps

    if spec.payment.paid_status == PaidStatus.PAID:
        paid = invoice.node("invoice.paid")
        if paid.toggle != "on":
            UIAWrapper(paid.element).toggle()
            actions.wait_until(lambda: True if invoice.node("invoice.paid").toggle == "on" else None,
                               timeout=5.0, what="paid ticked")
            actions.wait_until(lambda: invoice.node("invoice.paid_date"), timeout=5.0, what="the paid date field")
        if not shows(invoice.node("invoice.paid_date").value or "", spec.payment.payment_date):
            _enter_paid_date(invoice, spec.payment.payment_date)
        total = _money(money, invoice.node("order.total").value or "")
        if total is not None and _money(money, invoice.node("invoice.paid_value").value or "") != total:
            inputs.type_text(invoice.node("invoice.paid_value"), f"{total:.2f}", invoice.main.hwnd)
            _blur(invoice)
        snap = invoice.snapshot()

        def v(key: str) -> str:
            node = invoice.registry.resolve(snap, key)
            return (node.toggle if key == "invoice.paid" else node.value) or ""

        problems = paid_problems(v("invoice.paid"), v("invoice.paid_date"), _money(money, v("invoice.paid_value")),
                                 _money(money, v("order.total")), spec)
        detail = f"paid, at {v('invoice.paid_date')}, Value {v('invoice.paid_value')}"
    else:
        state = invoice.node("invoice.paid").toggle or ""
        problems = paid_problems(state, "", None, None, spec)
        detail = f"{spec.payment.paid_status.value}: paid left clear, no date or value"
    steps.append(Step("Invoice paid status", not problems, "; ".join(problems) or detail))
    return steps


@dataclass
class InvoiceRun:
    steps: list[Step]
    order_number: str | None = None
    invoice: OrderEditor | None = None  # the bound, still unsaved Invoice editor
    saved: bool = False  # a Save was dispatched
    number: str | None = None  # the saved Invoice's number
    description: str | None = None  # the payment term's description, read before the Invoice was opened

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


def open_invoice(main: MainWindow, registry: Registry, spec: OrderSpec, money: MoneyFormat,
                 ocr: Callable, *, order_number: str | None = None, unique: bool = True) -> InvoiceRun:
    """Parts B and C: open the linked Invoice, confirm the copy, set payment; never saves."""
    run = InvoiceRun([])
    try:
        term = check_payment(main, registry, spec.payment.method, ocr)  # before the Invoice exists
        description = term.seen.get("Description", "")
        run.description = description or None
        if term.kind != "reusable" or term.error or not description:
            run.steps.append(Step("payment term", False, f"needed for 5.2, review: {term.summary()}"))
            return run
        run.steps.append(Step("payment term", True, f"{term.reason}; listed as {description!r}"))
        order, number = open_saved_order(main, registry, spec, ocr, order_number, unique=unique)
        run.order_number = number
        run.steps.append(Step("source Order", True, f"{number} verified ({spec.order.external_reference!r})"))
        run.invoice = open_linked_invoice(order)
        run.steps.append(Step("linked Invoice", True, f"follow-up Invoice of {number} opened (unsaved)"))
        run.steps += check_copy(run.invoice, spec, money, ocr)
        if run.ok:
            run.steps += set_payment(run.invoice, spec, description, money)
    except (InvoiceError, EditorError, TableError, actions.ActionError, inputs.InputError, DateEntryError,
            HeaderError, TimeoutError) as e:
        run.steps.append(Step("linked Invoice", False, str(e)))
    return run


# --- part D: Save once ---------------------------------------------------------------------------------


def save_invoice(main: MainWindow, registry: Registry, invoice: OrderEditor) -> tuple[Step, str | None]:
    """5.4: the Invoice address tab selected (as for the Order: saving with Delivery selected
    crashed 2.2.0), then the toolbar Save ONCE. Returns the saved title (e.g. 'INV000001')."""
    selected = [n.name for n in invoice.snapshot().root.walk()
                if n.control_type == "TabItem" and n.selected and n.name in ("Invoice address", "Delivery address")]
    if selected != ["Invoice address"]:
        return Step("save Invoice", False, f"selected address tab is {selected}; not saving"), None
    number = invoice.node("order.no").value or ""
    try:
        title = RecordEditor(main, registry, invoice.hwnd, NEW_INVOICE).save()
    except EditorError as e:
        return Step("save Invoice", False, f"{e} (do NOT rerun before checking Documents by hand)"), None
    if title != number:
        return Step("save Invoice", False, f"saved, but the editor is titled {title!r}, not {number!r}"), title
    return Step("save Invoice", True, f"saved once: {title}"), title


# --- part D: verification after Save (5.5, 5.6) ---------------------------------------------------------

PAID_STATE = "COMMAND_CHECKED"  # Documents shows 'paid' with a tick (recorded 2026-11-06, INV000001)


def invoice_row_problems(rows: list[dict], number: str, spec: OrderSpec, contact: str, *,
                         unique: bool = True) -> list[str]:
    """Pure: Documents > Invoices holds the saved Invoice once, as specified (5.5). The row's Date
    is the Invoice Date (proposed, today), so it is not compared with the order date."""
    mine = [r for r in rows if r.get("Document") == number]
    if len(mine) != 1:
        return [f"{len(mine)} Invoices rows are {number!r}"]
    r, p = mine[0], []
    ref = spec.order.external_reference
    if r.get("Cust.Ref.") != ref:
        p.append(f"Cust.Ref. is {r.get('Cust.Ref.')!r}")
    same_ref = [x.get("Document") for x in rows if x.get("Cust.Ref.") == ref]
    if unique and len(same_ref) != 1:
        p.append(f"{len(same_ref)} Invoices carry Cust.Ref. {ref!r}: {same_ref}")
    if r.get("Name") != contact:
        p.append(f"Name is {r.get('Name')!r}, expected {contact!r}")
    if model_amount(r.get("Total")) != spec.derived.totals.gross:
        p.append(f"Total is {r.get('Total')!r}, expected {spec.derived.totals.gross}")
    paid = spec.payment.paid_status == PaidStatus.PAID
    if paid and r.get("State") != PAID_STATE:
        p.append(f"State is {r.get('State')!r}, expected paid ({PAID_STATE})")
    if not paid and r.get("State") == PAID_STATE:
        p.append(f"State is paid ({PAID_STATE}), but the status is {spec.payment.paid_status.value}")
    return [f"Invoices {number}: {x}" for x in p]


def invoices_with_ref(main: MainWindow, registry: Registry, ocr: Callable, ref: str) -> list[str] | None:
    """Invoice numbers carrying ``ref``; [] when the workspace has no Invoice yet (Documents then
    has no 'Invoices' category); None when the list is unreadable."""
    try:
        _grid, table = read_view(main, registry, "invoices", ocr)
    except EditorError as e:
        return [] if "0 filter entries named 'Invoices'" in str(e) else None
    except (TimeoutError, actions.ActionError):
        return None
    return [r.get("Document") or "?" for r in table.rows if r.get("Cust.Ref.") == ref]


def read_saved_invoice(main: MainWindow, registry: Registry, ocr: Callable, number: str) -> dict[str, str]:
    """5.6: reopen the saved Invoice from Documents (an open tab of it is closed first, unchanged)
    and read the persisted payment fields; the editor is closed unchanged afterwards."""
    for hwnd in _panes(main, number):
        RecordEditor(main, registry, hwnd, number).close_unchanged("order.cust_ref")
    grid, table = read_view(main, registry, "invoices", ocr)
    rows = [i for i, r in enumerate(table.rows) if r.get("Document") == number]
    if len(rows) != 1:
        raise InvoiceError(f"{len(rows)} Invoices rows are {number!r}")
    editor = RecordEditor.open_row(main, registry, grid, table, rows[0], editor_name=number)
    try:
        snap = editor.snapshot()
        seen = {}
        for key in ("invoice.payment", "invoice.paid", "invoice.paid_date", "invoice.paid_value", "order.total"):
            try:
                node = registry.resolve(snap, key)
                seen[key] = (node.toggle if key == "invoice.paid" else node.value) or ""
            except Exception:  # noqa: BLE001 - the paid fields exist only when paid is ticked
                seen[key] = ""
    finally:
        editor.close_unchanged("order.cust_ref")
    return seen


def verify_invoice(main: MainWindow, registry: Registry, spec: OrderSpec, money: MoneyFormat, ocr: Callable,
                   number: str | None = None, description: str | None = None, *,
                   order_number: str | None = None, unique: bool = True) -> list[Step]:
    """Read-only: the Invoices row, the still-open source Order, the reopened Invoice."""
    from fakturama_agent.flow.create_debtor import debtor_plan, saved_title

    ref = spec.order.external_reference
    contact = saved_title(debtor_plan(spec, ""))
    steps = []
    try:
        _grid, table = read_view(main, registry, "invoices", ocr)
    except (EditorError, TimeoutError, actions.ActionError) as e:
        return [Step("Invoice in Documents", False, f"cannot read Documents > Invoices: {e}")]
    if number is None:
        found = [r.get("Document") for r in table.rows if r.get("Cust.Ref.") == ref]
        if len(found) != 1:
            return [Step("Invoice in Documents", False, f"{len(found)} Invoices carry Cust.Ref. {ref!r}: {found}")]
        number = found[0]
    problems = invoice_row_problems(table.rows, number, spec, contact, unique=unique)
    state = "paid" if spec.payment.paid_status == PaidStatus.PAID else "not paid"
    steps.append(Step("Invoice in Documents", not problems, "; ".join(problems) or
                      f"{number}: {ref}, {contact}, {state}, total {spec.derived.totals.gross}"))
    order = verify_documents(main, registry, spec, ocr, order_number, unique=unique)
    steps.append(Step("source Order", order.ok, order.detail + (" (still open)" if order.ok else "")))
    if description is None:
        term = check_payment(main, registry, spec.payment.method, ocr)
        description = term.seen.get("Description", "")
        if term.kind != "reusable" or term.error or not description:
            steps.append(Step("saved Invoice reopened", False, f"cannot read the payment term: {term.summary()}"))
            return steps
    try:
        seen = read_saved_invoice(main, registry, ocr, number)
    except (InvoiceError, EditorError, TableError, actions.ActionError, inputs.InputError, TimeoutError) as e:
        steps.append(Step("saved Invoice reopened", False, str(e)))
        return steps
    problems = [] if seen["invoice.payment"] == description else [
        f"payment is {seen['invoice.payment']!r}, expected {description!r}"]
    problems += paid_problems(seen["invoice.paid"], seen["invoice.paid_date"],
                              _money(money, seen["invoice.paid_value"]), _money(money, seen["order.total"]), spec)
    detail = (f"{seen['invoice.payment']}, paid {seen['invoice.paid']}, at {seen['invoice.paid_date'] or '-'}, "
              f"Value {seen['invoice.paid_value'] or '-'}")
    steps.append(Step("saved Invoice reopened", not problems, "; ".join(problems) or detail))
    return steps


def create_invoice(main: MainWindow, registry: Registry, spec: OrderSpec, money: MoneyFormat, ocr: Callable, *,
                   allow_duplicate: bool = False, save: bool = True, order_number: str | None = None) -> InvoiceRun:
    """Parts B-D: duplicate guard, open + confirm + payment, Save once, verify."""
    ref = spec.order.external_reference
    existing = invoices_with_ref(main, registry, ocr, ref)
    if existing is None:
        return InvoiceRun([Step("invoice guard", False, "Documents > Invoices is unreadable")])
    if existing and not allow_duplicate:
        return InvoiceRun([Step("invoice guard", False, f"Invoices {existing} already carry Cust.Ref. {ref!r}; "
                                                        "not creating another (--allow-duplicate overrides)")])
    guard = Step("invoice guard", True, f"no Invoice carries {ref!r}" if not existing else
                 f"overridden: {existing} exist")
    unique = not allow_duplicate
    run = open_invoice(main, registry, spec, money, ocr, order_number=order_number, unique=unique)
    run.steps.insert(0, guard)
    if not run.ok or run.invoice is None:
        return run
    if not save:
        run.steps.append(Step("save Invoice", True, "skipped (--no-save): the Invoice is left open, unsaved"))
        return run
    run.saved = True
    step, title = save_invoice(main, registry, run.invoice)
    run.steps.append(step)
    if not step.ok:
        return run
    run.number, run.invoice = title, None
    run.steps += verify_invoice(main, registry, spec, money, ocr, number=title, description=run.description,
                                order_number=run.order_number, unique=unique)
    return run
