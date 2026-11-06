"""Creating master data: field rules, the create steps with fakes, the command (no Fakturama)."""

from decimal import Decimal
from pathlib import Path

import pytest

import fakturama_agent.flow.create_master as cm
from fakturama_agent.flow.create_master import (
    Created,
    judge_product_row,
    payment_fields,
    product_fields,
    vat_fields,
    verify_fields,
)
from fakturama_agent.flow.master_data import MasterCheck
from fakturama_agent.spec.models import OrderSpec, PaymentMethod
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_create
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat
from fakturama_agent.uia.record_editor import EditorError
from fakturama_agent.uia.tables import parse_tsv
from fakturama_agent.uia.tree import Snapshot

ROOT = Path(__file__).parent
FIX = ROOT / "fixtures"
GOLDEN = ROOT / "golden" / "order_001.json"
REG = Registry.load()
SPEC = OrderSpec.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
CHAIR, MAT = SPEC.items
US = MoneyFormat.from_example("$-1,234.57", 2)
MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama", workspace=None, minimized=False)


def recorded(name: str, keys: dict[str, str]) -> dict[str, str]:
    snap = Snapshot.load(FIX / "uia" / f"{name}.json")
    return {label: REG.resolve(snap, key).value or "" for label, key in keys.items()}


# --- field rules ------------------------------------------------------------------------------


def test_fresh_payment_editor_needs_name_description_and_code():
    fresh = recorded("editor_payment_new", {"Name": "payment.name", "Description": "payment.description",
                                            "Payment code": "payment.code", "Cash discount": "payment.discount",
                                            "Discount Days": "payment.discount_days", "Net Days": "payment.net_days"})
    problems = verify_fields(fresh, payment_fields(PaymentMethod.BANK_TRANSFER))
    assert [p.split(" ")[0] for p in problems] == ["Name", "Description", "Payment"]  # zeros are the defaults
    filled = {**fresh, "Name": "Bank Transfer", "Description": "Bank Transfer", "Payment code": "Credit transfer "}
    assert verify_fields(filled, payment_fields(PaymentMethod.BANK_TRANSFER)) == []


def test_vat_fields():
    fresh = recorded("editor_vat_new", {"Name": "vat.name", "Description": "vat.description",
                                        "Value": "vat.value", "VAT code": "vat.code"})
    assert fresh["VAT code"] == "S (Standard rate)" and fresh["Value"] == "0%"
    assert {p.split(" ")[0] for p in verify_fields(fresh, vat_fields(Decimal("7")))} == {"Name", "Description", "Value"}
    filled = {**fresh, "Name": "VAT 7%", "Description": "VAT 7%", "Value": "7%"}
    assert verify_fields(filled, vat_fields(Decimal("7"))) == []
    assert verify_fields({**filled, "Value": "70%"}, vat_fields(Decimal("7")))


def test_product_fields_use_the_money_format():
    fresh = recorded("editor_product_new", {"Item Number": "product.item_no", "Name": "product.name",
                                            "Description": "product.description", "VAT": "product.vat",
                                            "Price (gross)": "product.gross_price",
                                            "cost price (net)": "product.cost_price", "Stock": "product.stock"})
    rules = product_fields(CHAIR, Decimal("297.50"), "VAT 19%", US)
    assert fresh["VAT"] == "Free of Tax" and fresh["Price (gross)"] == "$0.00"
    filled = {**fresh, "Item Number": "CHR-ERG-01", "Name": "Ergonomic Desk Chair",
              "Description": "Ergonomic Desk Chair", "VAT": "VAT 19%", "Price (gross)": "$297.50"}
    assert verify_fields(filled, rules) == []
    assert verify_fields({**filled, "Price (gross)": "$297.49"}, rules)  # one cent off
    assert verify_fields({**filled, "VAT": "Free of Tax"}, rules)


def test_products_view_row_rules():
    rows = parse_tsv((FIX / "tables" / "copy_products.txt").read_bytes().decode(), REG.table("products"))
    sku001 = rows[0]  # SKU-001 Blue notebook, Price 11.899999999999999, VAT 19%
    item = CHAIR.model_copy(update={"sku": "SKU-001", "description": "Blue notebook"})
    assert judge_product_row(sku001, item, Decimal("11.90"), "VAT 19%") == []  # the double rounds to 11.90
    assert judge_product_row(sku001, item, Decimal("11.89"), "VAT 19%")
    assert judge_product_row(sku001, item, Decimal("11.90"), "VAT 7%")


# --- create steps with fakes -----------------------------------------------------------------------


class FakeEditor:
    log: list = []
    fields: dict = {}
    save_error: str | None = None

    def __init__(self, name):
        self.name = name
        self.registry = REG
        self.main = MAIN

    @classmethod
    def open_new(cls, main, registry, button_key, editor_name):
        FakeEditor.log.append(("open", editor_name))
        return cls(editor_name)

    def value(self, key):
        return FakeEditor.fields.get(key, "")

    def save(self):
        FakeEditor.log.append(("save", self.name))
        if FakeEditor.save_error:
            raise EditorError(FakeEditor.save_error)
        self.name = FakeEditor.fields.get("saved_as", "")  # the real editor takes the record's name
        return self.name

    def close_unchanged(self, key):
        FakeEditor.log.append(("close", self.name))


@pytest.fixture
def fake(monkeypatch):
    FakeEditor.log, FakeEditor.fields, FakeEditor.save_error = [], {}, None
    state = {"checks": []}
    monkeypatch.setattr(cm, "RecordEditor", FakeEditor)
    monkeypatch.setattr(cm, "LiveGrid", lambda main, registry: type("G", (), {"show": lambda self, spec: None})())
    monkeypatch.setattr(cm, "_type", lambda editor, key, text, blur: FakeEditor.log.append(("type", key, text)))
    monkeypatch.setattr(cm, "_choose", lambda editor, key, item, strip=False: FakeEditor.log.append(("choose", key, item)))
    monkeypatch.setattr(cm, "check_payment", lambda *a: state["checks"].pop(0))
    monkeypatch.setattr(cm, "check_vat", lambda *a: state["checks"].pop(0))
    return state


BANK_FIELDS = {"payment.name": "Bank Transfer", "payment.description": "Bank Transfer",
               "payment.code": "Credit transfer ", "payment.discount": "0%", "payment.discount_days": "0",
               "payment.net_days": "0", "saved_as": "Bank Transfer"}


def test_payment_created_saved_once_and_rechecked(fake):
    fake["checks"] = [MasterCheck("Payment Bank Transfer", "missing", "none"),
                      MasterCheck("Payment Bank Transfer", "reusable", "Bank Transfer has code 'Credit transfer'")]
    FakeEditor.fields = BANK_FIELDS
    result = cm.create_payment(MAIN, REG, PaymentMethod.BANK_TRANSFER, ocr=None)
    assert result.status == "created" and result.saved
    assert ("choose", "payment.code", "Credit transfer") in FakeEditor.log
    assert [e for e in FakeEditor.log if e[0] == "save"] == [("save", "New Term of Payment")]
    assert FakeEditor.log[-1] == ("close", "Bank Transfer")


def test_existing_payment_is_never_recreated(fake):
    fake["checks"] = [MasterCheck("Payment Bank Transfer", "reusable", "ok")]
    assert cm.create_payment(MAIN, REG, PaymentMethod.BANK_TRANSFER, ocr=None).status == "exists"
    assert FakeEditor.log == []


def test_conflicting_payment_is_review_and_untouched(fake):
    fake["checks"] = [MasterCheck("Payment Bank Transfer", "conflict", "payment code is empty")]
    result = cm.create_payment(MAIN, REG, PaymentMethod.BANK_TRANSFER, ocr=None)
    assert result.status == "review" and FakeEditor.log == []


def test_wrong_readback_is_not_saved(fake):
    fake["checks"] = [MasterCheck("Payment Bank Transfer", "missing", "none")]
    FakeEditor.fields = {**BANK_FIELDS, "payment.code": "Mutually defined "}
    result = cm.create_payment(MAIN, REG, PaymentMethod.BANK_TRANSFER, ocr=None)
    assert result.status == "stopped" and not result.saved and "Payment code" in result.detail
    assert not [e for e in FakeEditor.log if e[0] == "save"]


def test_unclear_save_is_reported_never_retried(fake):
    fake["checks"] = [MasterCheck("Payment Bank Transfer", "missing", "none")]
    FakeEditor.fields, FakeEditor.save_error = BANK_FIELDS, "Save outcome unclear"
    result = cm.create_payment(MAIN, REG, PaymentMethod.BANK_TRANSFER, ocr=None)
    assert result.status == "stopped" and result.saved
    assert len([e for e in FakeEditor.log if e[0] == "save"]) == 1


def test_saved_but_check_disagrees_is_review(fake):
    fake["checks"] = [MasterCheck("VAT 7%", "missing", "none"), MasterCheck("VAT 7%", "conflict", "Value shows '70%'")]
    FakeEditor.fields = {"vat.name": "VAT 7%", "vat.description": "VAT 7%", "vat.value": "7%",
                         "vat.code": "S (Standard rate)", "saved_as": "VAT 7%"}
    result = cm.create_vat(MAIN, REG, Decimal("7"), ocr=None)
    assert result.status == "review" and result.saved
    assert ("type", "vat.value", "7") in FakeEditor.log


def test_product_vat_chosen_before_gross_price(fake, monkeypatch):
    rows = {"before": [], "after": [{"Item No.": "CHR-ERG-01", "Name": "Ergonomic Desk Chair",
                                      "Price": "297.5", "VAT": "VAT taxValue: [0.19] name: [VAT 19%]"}]}
    calls = iter(["before", "after"])
    monkeypatch.setattr(cm, "product_rows", lambda *a: rows[next(calls)])
    fake["checks"] = [MasterCheck("VAT 19%", "reusable", "ok")]
    FakeEditor.fields = {"product.item_no": "CHR-ERG-01", "product.name": "Ergonomic Desk Chair",
                         "product.description": "Ergonomic Desk Chair", "product.vat": "VAT 19%",
                         "product.gross_price": "$297.50", "product.cost_price": "$0.00", "product.stock": "0.00",
                         "saved_as": "Ergonomic Desk Chair"}
    result = cm.create_product(MAIN, REG, CHAIR, Decimal("297.50"), US, ocr=None)
    assert result.status == "created", result.detail
    order = [e[1] for e in FakeEditor.log if e[0] in ("type", "choose")]
    assert order.index("product.vat") < order.index("product.gross_price")
    assert ("type", "product.gross_price", "297.50") in FakeEditor.log


def test_product_without_its_vat_is_not_created(fake, monkeypatch):
    monkeypatch.setattr(cm, "product_rows", lambda *a: [])
    fake["checks"] = [MasterCheck("VAT 19%", "missing", "none")]
    result = cm.create_product(MAIN, REG, CHAIR, Decimal("297.50"), US, ocr=None)
    assert result.status == "stopped" and "VAT is not ready" in result.detail and FakeEditor.log == []


def test_existing_product_with_other_price_is_review(fake, monkeypatch):
    monkeypatch.setattr(cm, "product_rows", lambda *a: [{"Item No.": "CHR-ERG-01", "Name": "Ergonomic Desk Chair",
                                                         "Price": "250.0", "VAT": "name: [VAT 19%]"}])
    result = cm.create_product(MAIN, REG, CHAIR, Decimal("297.50"), US, ocr=None)
    assert result.status == "review" and "Price" in result.detail and FakeEditor.log == []


# --- command ------------------------------------------------------------------------------------------


class Report:
    def __init__(self, ok=True):
        from fakturama_agent.uia.preflight import Check

        self.ok = ok
        self.money = US
        self.checks = [Check("unsaved editors", "ok" if ok else "failed", "open tabs: ['*New TAX Rate']")]


def run(capsys, kind, results=None, preflight_ok=True, **kw):
    calls = []
    results = dict(results or {})

    def maker(what):
        def create(main, registry, *args, **kwargs):
            key = {"product": lambda a: a.sku, "debtor": lambda a: a.debtor.company}.get(what, lambda a: a)(args[0])
            calls.append((what, key) + ((kwargs,) if kwargs else ()))
            return results.get((what, key), Created(f"{what}", "created", "ok", saved=True))
        return create

    creators = {w: maker(w) for w in ("payment", "vat", "product", "debtor")}
    code = cmd_create(kind, registry=REG, finder=lambda: MAIN, preflight=lambda m, r: Report(preflight_ok),
                      creators=creators, ocr=object(), **kw)
    return code, calls, capsys.readouterr()


def test_create_master_order(capsys):
    code, calls, _ = run(capsys, "master", spec_path=GOLDEN)
    assert code == 0
    assert calls == [("payment", PaymentMethod.BANK_TRANSFER), ("vat", Decimal("19")),
                     ("product", "CHR-ERG-01"), ("product", "MAT-DESK-02"),
                     ("debtor", "Northstar Office GmbH", {"without_email": False})]


def test_stop_ends_the_run(capsys):
    stop = {("vat", Decimal("19")): Created("VAT 19%", "review", "conflicting")}
    code, calls, out = run(capsys, "master", results=stop, spec_path=GOLDEN)
    assert code == 2 and calls[-1] == ("vat", Decimal("19")) and "3 later step(s) not run" in out.out


def test_unclear_save_warning(capsys):
    bad = {("payment", PaymentMethod.BANK_TRANSFER): Created("Payment Bank Transfer", "stopped", "unclear", saved=True)}
    code, _, out = run(capsys, "master", results=bad, spec_path=GOLDEN)
    assert code == 2 and "do not rerun blindly" in out.out


def test_preflight_must_be_clean(capsys):
    code, calls, out = run(capsys, "payment", preflight_ok=False, method="Bank Transfer")
    assert code == 2 and calls == [] and "Not creating anything" in out.out


def test_single_product_and_unknown_sku(capsys):
    assert run(capsys, "product", spec_path=GOLDEN, sku="MAT-DESK-02")[1] == [("product", "MAT-DESK-02")]
    assert run(capsys, "product", spec_path=GOLDEN, sku="NOPE")[0] == 1


def test_create_debtor_passes_the_email_choice(capsys):
    code, calls, _ = run(capsys, "debtor", spec_path=GOLDEN, without_email=True)
    assert code == 0 and calls == [("debtor", "Northstar Office GmbH", {"without_email": True})]


def test_product_gross_price_is_typed_in_the_german_format(fake, monkeypatch):
    """Live 2026-11-06 (locale Germany): '297.50' was read as 29.750,00 €, so the run stopped before Save."""
    germany = MoneyFormat.from_example("-1.234,57 €", 2)
    rows = {"before": [], "after": [{"Item No.": "CHR-ERG-01", "Name": "Ergonomic Desk Chair",
                                      "Price": "297.5", "VAT": "VAT taxValue: [0.19] name: [VAT 19%]"}]}
    calls = iter(["before", "after"])
    monkeypatch.setattr(cm, "product_rows", lambda *a: rows[next(calls)])
    fake["checks"] = [MasterCheck("VAT 19%", "reusable", "ok")]
    FakeEditor.fields = {"product.item_no": "CHR-ERG-01", "product.name": "Ergonomic Desk Chair",
                         "product.description": "Ergonomic Desk Chair", "product.vat": "VAT 19%",
                         "product.gross_price": "297,50\xa0€", "product.cost_price": "0,00\xa0€",
                         "product.stock": "0.00", "saved_as": "Ergonomic Desk Chair"}
    result = cm.create_product(MAIN, REG, CHAIR, Decimal("297.50"), germany, ocr=None)
    assert result.status == "created", result.detail
    assert ("type", "product.gross_price", "297,50") in FakeEditor.log
