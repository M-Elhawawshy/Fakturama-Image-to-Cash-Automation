"""Part H: create the missing Debtor of an OrderSpec. Persists one contact.

Steps (same discipline as part G):
1. check first in Data > Debtors (flow.match.decide_debtor on Company, First Name,
   Name, ZIP, City): select -> reuse; conflict/ambiguous -> review; not found -> create;
2. the term of payment must exist and match (part F); the Debtor's Payment
   dropdown lists terms by their *description*, read from that term;
3. New Debtor editor: keep the proposed Customer ID; Company, first and last name
   (real key events, blur on Customer ID: Tab would type a tab into Company, R5);
   Main address = billing address (role Invoice, + Delivery when billing equals
   delivery); otherwise '+' adds 'additional address #1' = delivery address with
   role Delivery; Miscellaneous: alias, Payment, Net; discount stays 0%;
4. Fakturama's validation marks are checked on every typed field. A rejected
   e-mail (e.g. the '.test' domain, R10) stops before saving, unless the caller
   allows saving without it (``without_email``: the field is cleared and the
   report says so). Decision 5;
5. every field is read back before Save; Save once, never retried;
6. Data > Debtors must then show exactly this contact under the kept Customer ID,
   and the saved contact is reopened and read back field by field (R10 saw a
   contact persisted without Company/E-Mail).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fakturama_agent.flow.create_master import Created, _choose, _type, _zero_percent, verify_fields
from fakturama_agent.flow.master_data import check_payment
from fakturama_agent.flow.match import DebtorKey, Decision, decide_debtor
from fakturama_agent.flow.order_select import debtor_key
from fakturama_agent.spec.models import Address, OrderSpec
from fakturama_agent.uia import actions, inputs
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.marks import field_marked
from fakturama_agent.uia.record_editor import EditorError, RecordEditor, read_view
from fakturama_agent.uia.tree import UiNode, snapshot_hwnd

INVOICE, DELIVERY = "Invoice address", "Delivery address"
ROLES = (INVOICE, DELIVERY)
MAIN_TAB, SECOND_TAB = "Main address", "additional address #1"
OUTER_TABS = ("Addresses", "Miscellaneous", "Notice")


class ReviewNeeded(RuntimeError):
    """Fakturama refuses a value: nothing is saved, a person decides."""


# --- what goes where (pure) --------------------------------------------------------------


@dataclass(frozen=True)
class AddressPlan:
    tab: str
    additional_name: str
    street: str
    zip: str
    city: str
    country: str
    email: str = ""
    phone: str = ""
    roles: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DebtorPlan:
    company: str
    first_name: str
    last_name: str
    addresses: tuple[AddressPlan, ...]
    alias: str
    payment: str  # the term's description, as the Payment dropdown lists it
    net_gross: str = "Net"
    notes: tuple[str, ...] = field(default_factory=tuple)


def _address(tab: str, a: Address, company: str, roles: set[str], email: str = "", phone: str = "") -> AddressPlan:
    # the company is the contact's own name; a different address name goes to 'additional name'
    extra = "" if a.name == company else a.name
    return AddressPlan(tab, extra, a.street, a.zip, a.city, a.country, email, phone, frozenset(roles))


def debtor_plan(spec: OrderSpec, payment_description: str, *, keep_email: bool = True) -> DebtorPlan:
    d = spec.debtor
    same = spec.delivery_address is None or spec.derived.billing_equals_delivery
    roles = {INVOICE, DELIVERY} if same else {INVOICE}
    email = (d.email or "") if keep_email else ""
    addresses = [_address(MAIN_TAB, spec.billing_address, d.company, roles, email, d.phone or "")]
    if not same:
        addresses.append(_address(SECOND_TAB, spec.delivery_address, d.company, {DELIVERY}))
    notes = () if keep_email or not d.email else (f"e-mail {d.email!r} left empty (Fakturama rejects it)",)
    return DebtorPlan(d.company, d.contact_first_name, d.contact_last_name, tuple(addresses), d.alias or "",
                      payment_description, notes=notes)


def roles_in(text: str) -> set[str]:
    return {r for r in ROLES if r in (text or "")}


def debtor_fields(plan: DebtorPlan, customer_id: str | None = None) -> dict:
    """Pure: the read-back each field must show (str = exact, callable = predicate)."""
    want: dict = {"Company": plan.company, "First Name": plan.first_name, "Last Name": plan.last_name}
    if customer_id is not None:
        want["Customer ID"] = customer_id
    want["address tabs"] = ", ".join(a.tab for a in plan.addresses)
    for a in plan.addresses:
        p = f"{a.tab}/"
        want.update({p + "additional name": a.additional_name, p + "Street": a.street, p + "ZIP": a.zip,
                     p + "City": a.city, p + "Country": a.country, p + "E-Mail": a.email, p + "Telephone": a.phone,
                     p + "address type": (lambda v, r=a.roles: roles_in(v) == r)})
    want.update({"Alias name": plan.alias, "Payment": plan.payment, "Discount": _zero_percent,
                 "Net or Gross": plan.net_gross})
    return want


# --- editor helpers (live) -----------------------------------------------------------------


def _tab_items(editor: RecordEditor) -> list[UiNode]:
    return [n for n in editor.snapshot().root.walk() if n.control_type == "TabItem"]


def _select_tab(editor: RecordEditor, name: str) -> None:
    def tab() -> UiNode:
        found = [n for n in _tab_items(editor) if n.name == name]
        if len(found) != 1:
            raise EditorError(f"{len(found)} tabs named {name!r} in the {editor.name!r} editor")
        return found[0]

    if tab().selected:
        return
    actions.guarded_click(tab(), editor.main.hwnd)
    actions.wait_until(lambda: True if tab().selected else None, timeout=5.0, what=f"tab {name!r} selected")
    time.sleep(0.3)


def address_tabs(editor: RecordEditor) -> list[str]:
    _select_tab(editor, "Addresses")
    return [n.name for n in _tab_items(editor) if n.name not in OUTER_TABS]


def _popup(main: MainWindow, before: set[int]) -> int | None:
    for w in process_windows(main.pid):
        if w.hwnd in before or w.title:
            continue
        if any(n.control_type == "CheckBox" and n.name == INVOICE for n in snapshot_hwnd(w.hwnd).root.walk()):
            return w.hwnd
    return None


def set_roles(editor: RecordEditor, roles: frozenset[str]) -> None:
    """Tick exactly ``roles`` in the address-type popup of the selected address tab."""
    import ctypes

    from pywinauto.controls.uiawrapper import UIAWrapper

    main = editor.main
    if roles_in(editor.value("address.roles")) == roles:
        return
    before = {w.hwnd for w in process_windows(main.pid)}
    actions.guarded_click(editor.registry.resolve(editor.snapshot(), "address.roles_button"), main.hwnd)  # R8
    popup = actions.wait_until(lambda: _popup(main, before), timeout=5.0, what="the address-type popup")

    def boxes() -> dict[str, UiNode]:
        return {n.name: n for n in snapshot_hwnd(popup).root.walk() if n.control_type == "CheckBox" and n.name in ROLES}

    for role, box in boxes().items():
        if (box.toggle == "on") != (role in roles):
            UIAWrapper(box.element).toggle()
            time.sleep(0.3)
    states = {role: box.toggle == "on" for role, box in boxes().items()}
    if ctypes.windll.user32.GetForegroundWindow() == popup:  # Escape closes it only while it has the focus
        inputs.send("{ESC}")
    try:
        actions.wait_until(lambda: True if not ctypes.windll.user32.IsWindowVisible(popup) else None,
                           timeout=5.0, what="the address-type popup to close")
    except TimeoutError:
        raise EditorError("the address-type popup did not close; close it by hand") from None
    if states != {r: r in roles for r in ROLES}:
        raise EditorError(f"address-type popup shows {states}, wanted {sorted(roles)}")
    shown = editor.value("address.roles")
    if roles_in(shown) != roles:
        raise EditorError(f"address type shows {shown!r}, wanted {sorted(roles)}")


def _marked(editor: RecordEditor, keys: list[str]) -> list[str]:
    actions.park_mouse(editor.main.hwnd)
    snap = editor.snapshot()
    return [k for k in keys if field_marked(editor.registry.resolve(snap, k))]


def fill_address(editor: RecordEditor, a: AddressPlan, *, drop_bad_email: bool) -> bool:
    """Fill the selected address tab; True when a rejected e-mail was cleared (``drop_bad_email``)."""
    blur = "address.street"
    typed = [("address.additional_name", a.additional_name), ("address.street", a.street),
             ("address.zip", a.zip), ("address.city", a.city), ("address.email", a.email),
             ("address.phone", a.phone)]
    for key, text in typed:
        if text:
            _type(editor, key, text, "address.additional_name" if key == blur else blur)
    _choose(editor, "address.country", a.country)
    set_roles(editor, a.roles)
    dropped = False
    marked = _marked(editor, [k for k, text in typed if text])
    if "address.email" in marked:
        if not drop_bad_email:
            raise ReviewNeeded(f"Fakturama rejects the e-mail {a.email!r} ('Please enter a valid e-mail address'); "
                               "nothing saved. Fix the e-mail in the spec, or rerun with --without-email to save "
                               "the Debtor with an empty E-Mail")
        _type(editor, "address.email", "", blur)
        if _marked(editor, ["address.email"]) or editor.value("address.email"):
            raise EditorError("could not clear the rejected e-mail")
        dropped = True
        marked.remove("address.email")
    if marked:
        raise ReviewNeeded(f"Fakturama marks {marked} as invalid in {a.tab!r}; nothing saved")
    return dropped


def read_debtor_editor(editor: RecordEditor) -> dict[str, str]:
    """Every field the plan sets, switching through the address tabs and Miscellaneous."""
    out = {"Customer ID": editor.value("debtor.customer_id"), "Company": editor.value("debtor.company"),
           "First Name": editor.value("debtor.first_name"), "Last Name": editor.value("debtor.last_name")}
    tabs = address_tabs(editor)
    out["address tabs"] = ", ".join(tabs)
    keys = {"additional name": "address.additional_name", "Street": "address.street", "ZIP": "address.zip",
            "City": "address.city", "Country": "address.country", "E-Mail": "address.email",
            "Telephone": "address.phone", "address type": "address.roles"}
    for tab in tabs:
        _select_tab(editor, tab)
        out.update({f"{tab}/{label}": editor.value(key) for label, key in keys.items()})
    _select_tab(editor, "Miscellaneous")
    out.update({"Alias name": editor.value("debtor.alias"), "Payment": editor.value("debtor.payment"),
                "Discount": editor.value("debtor.discount"), "Net or Gross": editor.value("debtor.net_gross")})
    _select_tab(editor, "Addresses")
    return out


def saved_title(plan: DebtorPlan) -> str:
    """Recorded: a saved contact's editor is titled e.g. 'Orion Study, Dan Taylor'."""
    return f"{plan.company}, {plan.first_name} {plan.last_name}"


# --- the step ----------------------------------------------------------------------------------


def create_debtor(main: MainWindow, registry, spec: OrderSpec, ocr: Callable, *,
                  without_email: bool = False) -> Created:
    key = debtor_key(spec)
    what = f"Debtor {key.company}"
    try:
        _grid, table = read_view(main, registry, "debtors", ocr)
    except EditorError as e:
        return Created(what, "stopped", f"cannot read Debtors: {e}")
    decision = view_decision(table.rows, spec_keys(spec))
    if decision.kind == "select":
        return Created(what, "exists", f"{decision.reason}; reused, not edited")
    if decision.kind != "not_found":
        return Created(what, "review", f"{decision.kind}: {decision.reason}")

    pay = check_payment(main, registry, spec.payment.method, ocr)
    if pay.kind != "reusable" or pay.error:
        return Created(what, "stopped", f"its term of payment is not ready (create-payment first): {pay.summary()}")
    plan = debtor_plan(spec, pay.seen.get("Description", ""))
    dropped = False
    try:
        editor = RecordEditor.open_new(main, registry, "toolbar.new_contact", "New Debtor")
        customer_id = editor.value("debtor.customer_id")
        if not customer_id:
            return Created(what, "stopped", "the New Debtor editor proposes no Customer ID; nothing saved")
        _type(editor, "debtor.company", plan.company, "debtor.customer_id")
        _type(editor, "debtor.first_name", plan.first_name, "debtor.customer_id")
        _type(editor, "debtor.last_name", plan.last_name, "debtor.customer_id")
        for i, address in enumerate(plan.addresses):
            if i == 0:
                _select_tab(editor, "Addresses")
                _select_tab(editor, MAIN_TAB)
            else:
                before = address_tabs(editor)
                actions.invoke(registry.resolve(editor.snapshot(), "debtor.add_address"))
                actions.wait_until(lambda: True if address.tab in address_tabs(editor) else None, timeout=5.0,
                                   what=f"tab {address.tab!r}")
                if address.tab in before:
                    raise EditorError(f"{address.tab!r} existed before '+' was pressed")
                _select_tab(editor, address.tab)
            dropped |= fill_address(editor, address, drop_bad_email=without_email)
        if dropped:
            plan = debtor_plan(spec, plan.payment, keep_email=False)
        _select_tab(editor, "Miscellaneous")
        if plan.alias:
            _type(editor, "debtor.alias", plan.alias, "debtor.discount")
        _choose(editor, "debtor.payment", plan.payment)
        _choose(editor, "debtor.net_gross", plan.net_gross)
        problems = verify_fields(read_debtor_editor(editor), debtor_fields(plan, customer_id))
    except ReviewNeeded as e:
        return Created(what, "review", f"{e} (the New Debtor editor is left open for you)")
    except (EditorError, inputs.InputError, actions.ActionError, TimeoutError) as e:
        return Created(what, "stopped", f"{e}; nothing saved (close the New Debtor editor by hand without saving)")
    if problems:
        return Created(what, "stopped", f"not saved, fields differ: {problems}")

    note = f" NOTE: {'; '.join(plan.notes)}" if plan.notes else ""
    try:
        title = editor.save()
        editor.close_unchanged("debtor.customer_id")
    except EditorError as e:
        return Created(what, "stopped", str(e), saved=True)
    return _verify_saved(main, registry, ocr, what, plan, customer_id, title, note)


def _verify_saved(main, registry, ocr, what: str, plan: DebtorPlan, customer_id: str, title: str,
                  note: str) -> Created:
    reopened = None
    try:
        grid, table = read_view(main, registry, "debtors", ocr)
        rows = [i for i, r in enumerate(table.rows) if r.get("No.") == customer_id]
        decision = view_decision(table.rows, plan_keys(plan))
        if len(rows) != 1 or decision.kind != "select" or decision.index != rows[0]:
            return Created(what, "review", f"saved as {title!r}, but Debtors shows {len(rows)} row(s) "
                                           f"{customer_id}: {decision.reason}{note}", saved=True)
        reopened = RecordEditor.open_row(main, registry, grid, table, rows[0], editor_name=title)
        seen = read_debtor_editor(reopened)
    except (EditorError, TimeoutError, actions.ActionError) as e:
        return Created(what, "review", f"saved as {title!r}, but reading it back failed: {e}{note}", saved=True)
    finally:
        if reopened is not None:
            try:
                reopened.close_unchanged("debtor.customer_id")
            except EditorError as e:
                note += f" (could not close the reopened editor: {e})"
    problems = verify_fields(seen, debtor_fields(plan, customer_id))
    if problems:
        return Created(what, "review", f"saved as {title!r} ({customer_id}), but it reads back: {problems}{note}",
                       saved=True)
    return Created(what, "created", f"saved as {title!r} ({customer_id}); read back field by field: "
                                    f"{len(plan.addresses)} address(es), payment {plan.payment!r}{note}", saved=True)


def view_decision(rows: list[dict], keys: list[DebtorKey]) -> Decision:
    """Pure: decide on Data > Debtors rows. Recorded 2026-11-05: for a contact with a
    delivery address the Debtors list shows the DELIVERY address's ZIP/City (the
    Order's 'Select the address' dialog shows the billing one). So a row matches when
    Company and names match and ZIP/City are those of any of the contact's addresses
    (``keys``: billing first). Everything else keeps decide_debtor's outcome."""
    wanted = [k.as_row() for k in keys]
    hits = [i for i, r in enumerate(rows) if any(all((r.get(f) or "") == v for f, v in w.items()) for w in wanted)]
    if len(hits) == 1:
        how = "" if decide_debtor(rows, keys[0]).kind == "select" else " (Debtors lists the delivery ZIP/City)"
        return Decision("select", hits[0], f"one exact match ({rows[hits[0]].get('No.')}){how}", rows)
    if len(hits) > 1:
        return Decision("ambiguous", None, f"{len(hits)} contacts match: {[rows[i].get('No.') for i in hits]}", rows)
    return decide_debtor(rows, keys[0])  # conflict (same company, other details) or not found


def plan_keys(plan: DebtorPlan) -> list[DebtorKey]:
    return [DebtorKey(plan.company, plan.first_name, plan.last_name, a.zip, a.city) for a in plan.addresses]


def spec_keys(spec: OrderSpec) -> list[DebtorKey]:
    keys = [debtor_key(spec)]
    d = spec.delivery_address
    if d is not None and not spec.derived.billing_equals_delivery:
        keys.append(DebtorKey(spec.debtor.company, spec.debtor.contact_first_name, spec.debtor.contact_last_name,
                              d.zip, d.city))
    return keys
