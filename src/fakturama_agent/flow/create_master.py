"""Part G: create a missing term of payment, VAT rate or product. Persists records.

Every create follows the same steps:
1. check first (part F / the Products view): only "missing" proceeds; an
   existing match is reused, anything else is review. The tool never edits an
   existing record;
2. open a new editor, fill it, read every field back BEFORE saving: a mismatch
   stops without saving (the unsaved editor is left for the user);
3. Save once (never retried: an unclear outcome stops for a manual check);
4. check again: the record must now be found and match;
5. close the saved editor unchanged.

Order matters (reference repo): terms of payment are created before any New
Debtor editor is opened (it reads its payment list once); a product's VAT must
exist before the product, and is chosen before the gross price, because
changing the VAT recalculates the gross price.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from fakturama_agent.flow.master_data import MasterCheck, check_payment, check_vat
from fakturama_agent.spec.derive import payment_code, vat_name
from fakturama_agent.spec.models import Item, PaymentMethod
from fakturama_agent.uia import inputs
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat, MoneyFormatError
from fakturama_agent.uia.record_editor import EditorError, RecordEditor, read_view
from fakturama_agent.uia.views import LiveGrid

Status = Literal["created", "exists", "review", "stopped"]


@dataclass
class Created:
    what: str
    status: Status
    detail: str
    saved: bool = False  # a Save was attempted: never repeat it blindly

    @property
    def ok(self) -> bool:
        return self.status in ("created", "exists")


class CreateError(RuntimeError):
    pass


def _from_check(check: MasterCheck) -> Created | None:
    """None when the record is missing (go ahead and create it)."""
    if check.error:
        return Created(check.what, "stopped", f"check failed: {check.error}")
    if check.kind == "reusable":
        return Created(check.what, "exists", check.reason)
    if check.kind == "missing":
        return None
    return Created(check.what, "review", check.summary())


def _type(editor: RecordEditor, key: str, text: str, blur_key: str) -> None:
    inputs.type_text(editor.registry.resolve(editor.snapshot(), key), text, editor.main.hwnd)
    inputs.focus(editor.registry.resolve(editor.snapshot(), blur_key), editor.main.hwnd)  # commit on blur
    time.sleep(0.2)


def _choose(editor: RecordEditor, key: str, item: str, *, strip: bool = False) -> None:
    inputs.choose(editor.registry.resolve(editor.snapshot(), key), item, editor.main.hwnd,
                  refresh=lambda: editor.registry.resolve(editor.snapshot(), key), strip=strip)


def verify_fields(seen: dict[str, str], wanted: dict[str, Callable[[str], bool] | str]) -> list[str]:
    """Pure: which read-back fields differ from what was wanted (str = exact, callable = predicate)."""
    problems = []
    for field, want in wanted.items():
        value = seen.get(field, "")
        ok = want(value) if callable(want) else value == want
        if not ok:
            problems.append(f"{field} shows {value!r}" + ("" if callable(want) else f", wanted {want!r}"))
    return problems


def _zero_percent(text: str) -> bool:
    return text.strip().rstrip("%").strip() in ("0", "0.0", "0.00")


def _save_and_recheck(editor: RecordEditor, what: str, expected_name: str, recheck: Callable[[], MasterCheck],
                      close_key: str) -> Created:
    try:
        name = editor.save()
    except EditorError as e:
        return Created(what, "stopped", str(e), saved=True)
    if name != expected_name:
        return Created(what, "stopped", f"after Save the editor shows {name!r}, expected {expected_name!r}", saved=True)
    try:
        editor.close_unchanged(close_key)
    except EditorError as e:
        return Created(what, "stopped", f"saved, but {e}", saved=True)
    after = recheck()
    if after.kind != "reusable" or after.error:
        return Created(what, "review", f"saved, but the check afterwards says: {after.summary()}", saved=True)
    return Created(what, "created", f"saved and verified: {after.reason}", saved=True)


# --- term of payment ------------------------------------------------------------------------


def payment_fields(method: PaymentMethod) -> dict:
    name = method.value
    return {"Name": name, "Description": name, "Payment code": lambda v: v.strip() == payment_code(method),
            "Cash discount": _zero_percent, "Discount Days": "0", "Net Days": "0"}


def read_payment_editor(editor: RecordEditor) -> dict[str, str]:
    keys = {"Name": "payment.name", "Description": "payment.description", "Payment code": "payment.code",
            "Cash discount": "payment.discount", "Discount Days": "payment.discount_days",
            "Net Days": "payment.net_days"}
    return {label: editor.value(key) for label, key in keys.items()}


def create_payment(main: MainWindow, registry: Registry, method: PaymentMethod, ocr: Callable) -> Created:
    what = f"Payment {method.value}"
    before = _from_check(check_payment(main, registry, method, ocr))
    if before is not None:
        return before
    try:
        LiveGrid(main, registry).show(registry.table("payments"))
        editor = RecordEditor.open_new(main, registry, "view.payments.create", "New Term of Payment")
        _type(editor, "payment.name", method.value, "payment.description")
        _type(editor, "payment.description", method.value, "payment.name")
        _choose(editor, "payment.code", payment_code(method), strip=True)
        problems = verify_fields(read_payment_editor(editor), payment_fields(method))
    except (EditorError, inputs.InputError, TimeoutError) as e:
        return Created(what, "stopped", f"{e}; nothing saved (close the new editor by hand without saving)")
    if problems:
        return Created(what, "stopped", f"not saved, fields differ: {problems}")
    return _save_and_recheck(editor, what, method.value, lambda: check_payment(main, registry, method, ocr),
                             "payment.name")


# --- VAT rate -------------------------------------------------------------------------------------


def vat_fields(rate: Decimal) -> dict:
    name = vat_name(rate)
    return {"Name": name, "Description": name,
            "Value": lambda v: v.strip().endswith("%") and Decimal(v.strip().rstrip("%")) == rate,
            "VAT code": lambda v: v.split(" ", 1)[0] == "S"}


def read_vat_editor(editor: RecordEditor) -> dict[str, str]:
    keys = {"Name": "vat.name", "Description": "vat.description", "Value": "vat.value", "VAT code": "vat.code"}
    return {label: editor.value(key) for label, key in keys.items()}


def create_vat(main: MainWindow, registry: Registry, rate: Decimal, ocr: Callable) -> Created:
    name = vat_name(rate)
    before = _from_check(check_vat(main, registry, rate, ocr))
    if before is not None:
        return before
    try:
        LiveGrid(main, registry).show(registry.table("vats"))
        editor = RecordEditor.open_new(main, registry, "view.vats.create", "New TAX Rate")
        _type(editor, "vat.name", name, "vat.description")
        _type(editor, "vat.description", name, "vat.name")
        _type(editor, "vat.value", f"{rate.normalize():f}", "vat.name")  # Value commits on blur (R7)
        _choose(editor, "vat.code", "S (Standard rate)")
        problems = verify_fields(read_vat_editor(editor), vat_fields(rate))
    except (EditorError, inputs.InputError, TimeoutError, ArithmeticError) as e:
        return Created(name, "stopped", f"{e}; nothing saved (close the new editor by hand without saving)")
    if problems:
        return Created(name, "stopped", f"not saved, fields differ: {problems}")
    return _save_and_recheck(editor, name, name, lambda: check_vat(main, registry, rate, ocr), "vat.name")


# --- product ----------------------------------------------------------------------------------------


def product_rows(main: MainWindow, registry: Registry, sku: str, ocr: Callable) -> list[dict]:
    _grid, table = read_view(main, registry, "products", ocr)
    return [r for r in table.rows if r.get("Item No.") == sku]


def judge_product_row(row: dict, item: Item, gross: Decimal, vat: str) -> list[str]:
    """Pure: does a Products-view row (model values) carry what the product should?"""
    problems = []
    if row.get("Name") != item.description:
        problems.append(f"Name is {row.get('Name')!r}")
    try:
        price = round(Decimal(row.get("Price") or ""), 2)
    except ArithmeticError:
        price = None
    if price != gross:
        problems.append(f"Price is {row.get('Price')!r}, expected {gross}")
    if f"name: [{vat}]" not in (row.get("VAT") or ""):
        problems.append(f"VAT is not {vat!r}")
    return problems


def product_fields(item: Item, gross: Decimal, vat: str, money: MoneyFormat) -> dict:
    def amount(expected: Decimal) -> Callable[[str], bool]:
        def check(text: str) -> bool:
            try:
                return money.parse(text) == expected
            except MoneyFormatError:
                return False
        return check

    return {"Item Number": item.sku, "Name": item.description, "Description": item.description, "VAT": vat,
            "Price (gross)": amount(gross), "cost price (net)": amount(Decimal("0")),
            "Stock": lambda v: Decimal(v.replace(",", "") or "0") == 0}


def read_product_editor(editor: RecordEditor) -> dict[str, str]:
    keys = {"Item Number": "product.item_no", "Name": "product.name", "Description": "product.description",
            "VAT": "product.vat", "Price (gross)": "product.gross_price", "cost price (net)": "product.cost_price",
            "Stock": "product.stock"}
    return {label: editor.value(key) for label, key in keys.items()}


def create_product(main: MainWindow, registry: Registry, item: Item, gross: Decimal, money: MoneyFormat,
                   ocr: Callable) -> Created:
    what = f"Product {item.sku}"
    vat = vat_name(item.vat_percent)
    try:
        existing = product_rows(main, registry, item.sku, ocr)
    except EditorError as e:
        return Created(what, "stopped", f"cannot read Products: {e}")
    if len(existing) > 1:
        return Created(what, "review", f"{len(existing)} products have Item No. {item.sku!r}")
    if existing:
        problems = judge_product_row(existing[0], item, gross, vat)
        if problems:
            return Created(what, "review", f"exists with other details: {problems}")
        return Created(what, "exists", f"{item.sku} exists with gross {gross} and {vat}")
    vat_check = check_vat(main, registry, item.vat_percent, ocr)
    if vat_check.kind != "reusable":
        return Created(what, "stopped", f"its VAT is not ready: {vat_check.summary()}")
    try:
        editor = RecordEditor.open_new(main, registry, "toolbar.new_product", "New product")
        _type(editor, "product.item_no", item.sku, "product.name")
        _type(editor, "product.name", item.description, "product.item_no")
        _type(editor, "product.description", item.description, "product.item_no")
        _choose(editor, "product.vat", vat)  # before the gross price: changing VAT recalculates gross
        _type(editor, "product.gross_price", f"{gross:.2f}", "product.item_no")
        problems = verify_fields(read_product_editor(editor), product_fields(item, gross, vat, money))
    except (EditorError, inputs.InputError, TimeoutError) as e:
        return Created(what, "stopped", f"{e}; nothing saved (close the new editor by hand without saving)")
    if problems:
        return Created(what, "stopped", f"not saved, fields differ: {problems}")
    try:
        name = editor.save()
        editor.close_unchanged("product.item_no")
        after = product_rows(main, registry, item.sku, ocr)
    except EditorError as e:
        return Created(what, "stopped", f"{e}", saved=True)
    if len(after) != 1 or judge_product_row(after[0], item, gross, vat):
        detail = judge_product_row(after[0], item, gross, vat) if len(after) == 1 else f"{len(after)} rows"
        return Created(what, "review", f"saved as {name!r}, but Products shows: {detail}", saved=True)
    return Created(what, "created", f"saved as {name!r}; Products shows gross {gross} and {vat}", saved=True)
