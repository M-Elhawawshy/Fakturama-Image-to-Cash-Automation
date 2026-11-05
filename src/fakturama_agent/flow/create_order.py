"""Part I: build the Order in a New Order editor, verify everything, Save once, verify Documents.

Order of steps (each must pass before the next; any failure stops BEFORE Save and
leaves the unsaved Order open for the user):
1. duplicate guard: no Order in Documents carries the Cust.Ref. (Decision 2);
2. header (part D) read back;
3. Debtor and products selected from their dialogs (part E): all must exist
   (``create-master`` creates them);
4. lines: type what differs (Qty., U.Price, Discount), read back twice, compare
   every cell with the spec and ``derived.expected_line_net``;
5. footer: overall Discount 0 %, free shipping 0.00, Total Net / VAT / Total
   equal the spec's derived totals;
6. addresses: Delivery tab shows the delivery address, then the Invoice tab is
   selected again and verified (R11);
7. Save ONCE (never retried; an unclear outcome or any dialog stops, R12);
8. Documents (Orders) holds exactly one row with the saved number, the Cust.Ref.,
   Date, contact, state open and Total.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fakturama_agent.flow.create_debtor import debtor_plan, saved_title
from fakturama_agent.flow.order_header import HeaderError, HeaderWanted, OrderEditor, compare
from fakturama_agent.flow.order_lines import (
    LineError,
    documents_problems,
    duplicates,
    footer_problems,
    invoice_tab_problems,
    lines_problems,
    read_footer,
    set_lines,
)
from fakturama_agent.flow.order_select import check_all, check_delivery, debtor_key
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat
from fakturama_agent.uia.record_editor import EditorError, RecordEditor, read_view
from fakturama_agent.uia.tables import TableError


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


@dataclass
class OrderRun:
    steps: list[Step] = field(default_factory=list)
    number: str | None = None
    saved: bool = False  # a Save was dispatched
    unsaved_left_open: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)

    def add(self, name: str, problems: list[str], ok_detail: str) -> bool:
        self.steps.append(Step(name, not problems, ok_detail if not problems else "; ".join(problems)))
        return not problems


def create_order(main: MainWindow, registry: Registry, spec: OrderSpec, money: MoneyFormat, ocr: Callable, *,
                 allow_duplicate: bool = False, save: bool = True) -> OrderRun:
    run = OrderRun()
    ref = spec.order.external_reference
    try:
        _grid, docs = read_view(main, registry, "documents", ocr)
    except EditorError as e:
        run.add("duplicate guard", [f"cannot read Documents: {e}"], "")
        return run
    dup = duplicates(docs.rows, ref)
    if dup and not allow_duplicate:
        run.add("duplicate guard", [f"Orders {dup} already carry Cust.Ref. {ref!r}; not creating another "
                                    "(--allow-duplicate overrides)"], "")
        return run
    run.add("duplicate guard", [], f"no Order carries {ref!r}" if not dup else f"overridden: {dup} exist")

    try:
        editor = OrderEditor.open_new(main, registry)
        run.unsaved_left_open = True
        header = editor.set_header(HeaderWanted(cust_ref=ref, order_date=spec.order.order_date))
        bad = [f"{c.field} shows {c.seen!r}" for c in compare(
            HeaderWanted(cust_ref=ref, order_date=spec.order.order_date), header) if not c.ok]
        if not run.add("header", bad, f"{header.number}, {header.date_shown}, {header.cust_ref}, "
                                      f"{header.price_mode}, {header.vat_mode}"):
            return run
        outcomes = check_all(editor, spec, ocr)
        bad = [f"{o.what}: {o.summary()}" for o in outcomes if not o.ok]
        if len(outcomes) != len(spec.items) + 1:
            bad.append("not every selection ran")
        if not run.add("Debtor and products", bad, f"selected the Debtor and {len(spec.items)} product(s)"):
            return run
        rows = set_lines(editor, spec, ocr)
        if not run.add("lines", lines_problems(rows, spec),
                       ", ".join(f"{i.sku} {i.quantity} x {i.unit_net_price} -{i.discount_percent}% = {d.expected_line_net}"
                                 for i, d in zip(spec.items, spec.derived.items, strict=True))):
            return run
        t = spec.derived.totals
        if not run.add("totals", footer_problems(read_footer(editor), spec, money),
                       f"net {t.net}, VAT {t.vat}, total {t.gross}; discount 0%, free shipping"):
            return run
        bad = check_delivery(editor, spec) if (spec.delivery_address and not spec.derived.billing_equals_delivery) else []
        bad += invoice_tab_problems(editor, debtor_key(spec))
        if not run.add("addresses", bad, "delivery address checked; Invoice address tab selected and verified"):
            return run
    except (HeaderError, LineError, TableError, EditorError, inputs.InputError, actions.ActionError,
            TimeoutError) as e:
        run.add("build the Order", [str(e)], "")
        return run

    if not save:
        run.add("save", [], "skipped (--no-save): the verified Order is left open, unsaved")
        return run
    record = RecordEditor(main, registry, editor.hwnd, "New Order")
    run.saved = True
    try:
        title = record.save()  # once; raises on any dialog or an unclear outcome
    except EditorError as e:
        run.add("save", [f"{e} (do NOT rerun before checking Documents by hand)"], "")
        return run
    run.unsaved_left_open = False
    try:
        number = registry.resolve(editor.snapshot(), "order.no").value or ""
    except Exception as e:  # noqa: BLE001 - reported, the Save is done
        run.add("save", [f"saved as {title!r}, but the number cannot be read: {e}"], "")
        return run
    run.number = number
    run.add("save", [] if number and number in title else [f"editor shows {title!r} for No. {number!r}"],
            f"saved once: editor {title!r}")
    run.steps.append(verify_documents(main, registry, spec, ocr, number))
    return run


def verify_documents(main: MainWindow, registry: Registry, spec: OrderSpec, ocr: Callable,
                     number: str | None = None) -> Step:
    """Read-only: Documents (Orders) holds the saved Order exactly once and it matches the spec.
    Without ``number`` the Order is found by its Cust.Ref. (``check-order``)."""
    ref = spec.order.external_reference
    try:
        _grid, docs = read_view(main, registry, "documents", ocr)
    except (EditorError, TimeoutError, actions.ActionError) as e:  # the Save is done: report, never crash
        return Step("Documents", False, f"cannot read Documents: {e}")
    if number is None:
        found = duplicates(docs.rows, ref)
        if len(found) != 1:
            return Step("Documents", False, f"{len(found)} Orders carry Cust.Ref. {ref!r}: {found}")
        number = found[0]
    contact = saved_title(debtor_plan(spec, ""))
    problems = documents_problems(docs.rows, number, spec, contact)
    return Step("Documents", not problems, "; ".join(problems) if problems else
                f"{number}: {ref}, {spec.order.order_date}, {contact}, open, total {spec.derived.totals.gross}")
