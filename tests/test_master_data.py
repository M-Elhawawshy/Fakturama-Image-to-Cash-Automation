"""VAT and payment-term checks: rules on recorded editors, orchestration with fakes, commands."""

from decimal import Decimal
from pathlib import Path

import pytest

import fakturama_agent.flow.master_data as md
from fakturama_agent.flow.master_data import MasterCheck, judge_payment, judge_vat, rows_named
from fakturama_agent.spec.models import PaymentMethod
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_check_master
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.record_editor import EditorError
from fakturama_agent.uia.tables import parse_tsv
from fakturama_agent.uia.tree import Snapshot

FIX = Path(__file__).parent / "fixtures"
REG = Registry.load()
MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama", workspace=None, minimized=False)
VAT_ROWS = parse_tsv((FIX / "tables" / "copy_vats.txt").read_bytes().decode(), REG.table("vats"))
PAY_ROWS = parse_tsv((FIX / "tables" / "copy_payments.txt").read_bytes().decode(), REG.table("payments"))


def editor_fields(name: str, keys: dict[str, str]) -> dict[str, str]:
    snap = Snapshot.load(FIX / "uia" / f"{name}.json")
    return {label: REG.resolve(snap, key).value or "" for label, key in keys.items()}


VAT_SHOWN = editor_fields("editor_vat_19", {"Name": "vat.name", "Value": "vat.value", "VAT code": "vat.code"})
CASH_SHOWN = editor_fields("editor_payment_cash", {"Name": "payment.name", "Payment code": "payment.code"})


def test_recorded_editors():
    assert VAT_SHOWN == {"Name": "VAT 19%", "Value": "19%", "VAT code": "S (Standard rate)"}
    assert CASH_SHOWN == {"Name": "Cash", "Payment code": "Mutually defined "}  # trailing space as shown


def test_vat_19_is_reusable():
    row = VAT_ROWS[rows_named(VAT_ROWS, "VAT 19%")[0]]
    assert judge_vat("VAT 19%", Decimal("19"), row["Value"], VAT_SHOWN) == ("reusable", "VAT 19% = 19%, code S")


@pytest.mark.parametrize(
    "table_value, shown_change, problem",
    [
        ("0.07", {}, "table Value"),
        ("0.19", {"Value": "7%"}, "Value shows"),
        ("0.19", {"VAT code": "Z (Zero rated goods)"}, "VAT code"),
        ("0.19", {"VAT code": ""}, "VAT code"),
        ("0.19", {"Name": "VAT 19 %"}, "editor Name"),
    ],
)
def test_vat_conflicts(table_value, shown_change, problem):
    kind, reason = judge_vat("VAT 19%", Decimal("19"), table_value, {**VAT_SHOWN, **shown_change})
    assert kind == "conflict" and problem in reason


def test_payment_codes():
    shown = {"Name": "Bank Transfer", "Payment code": "Credit transfer "}  # trailing space as Fakturama shows it
    assert judge_payment("Bank Transfer", "Credit transfer", shown)[0] == "reusable"
    kind, reason = judge_payment("Bank Transfer", "Credit transfer", {**shown, "Payment code": ""})
    assert kind == "conflict" and "empty" in reason  # the term you deleted had no code: a conflicting definition
    assert judge_payment("Bank Transfer", "Credit transfer", {**shown, "Payment code": "Mutually defined "})[0] == "conflict"
    assert judge_payment("Cash", "Credit transfer", CASH_SHOWN)[0] == "conflict"


def test_rows_named_is_exact():
    assert rows_named(VAT_ROWS, "VAT 19%") == [1]
    assert rows_named(VAT_ROWS, "vat 19%") == [] and rows_named(PAY_ROWS, "Bank Transfer") == []


# --- orchestration with a fake view and editor ------------------------------------------


class FakeRead:
    def __init__(self, rows):
        self.rows = rows
        self.view = "VATs"


class FakeEditor:
    instances: list = []

    def __init__(self, fields, close_error=None):
        self.fields = fields
        self.closed = False
        self.close_error = close_error
        FakeEditor.instances.append(self)

    def value(self, key):
        return self.fields[key]

    def close_unchanged(self, focus_key):
        if self.close_error:
            raise EditorError(self.close_error)
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    FakeEditor.instances = []
    state = {"rows": [], "fields": {}, "close_error": None, "read_error": None}

    def read_view(main, registry, table_key, ocr):
        if state["read_error"]:
            raise EditorError(state["read_error"])
        return object(), FakeRead(state["rows"])

    monkeypatch.setattr(md, "read_view", read_view)
    monkeypatch.setattr(md.RecordEditor, "open_row", classmethod(
        lambda cls, main, reg, grid, read, i: FakeEditor(state["fields"], state["close_error"])))
    return state


VAT_FIELDS = {"vat.name": "VAT 19%", "vat.value": "19%", "vat.code": "S (Standard rate)"}


def test_check_vat_reusable_and_editor_closed(fake):
    fake["rows"], fake["fields"] = VAT_ROWS, VAT_FIELDS
    out = md.check_vat(MAIN, REG, Decimal("19"), ocr=None)
    assert out.kind == "reusable" and out.ok
    assert FakeEditor.instances[0].closed


def test_check_vat_missing_never_opens_an_editor(fake):
    fake["rows"] = VAT_ROWS
    out = md.check_vat(MAIN, REG, Decimal("7"), ocr=None)
    assert out.kind == "missing" and out.ok and FakeEditor.instances == []


def test_two_rows_with_the_name_are_ambiguous(fake):
    fake["rows"] = [*VAT_ROWS, VAT_ROWS[1]]
    out = md.check_vat(MAIN, REG, Decimal("19"), ocr=None)
    assert out.kind == "ambiguous" and not out.ok and FakeEditor.instances == []


def test_zero_rate_is_unsupported(fake):
    out = md.check_vat(MAIN, REG, Decimal("0"), ocr=None)
    assert out.kind == "unsupported" and not out.ok


def test_payment_conflict_is_review(fake):
    fake["rows"] = [{"Name": "Bank Transfer"}]
    fake["fields"] = {"payment.name": "Bank Transfer", "payment.code": ""}
    out = md.check_payment(MAIN, REG, PaymentMethod.BANK_TRANSFER, ocr=None)
    assert out.kind == "conflict" and not out.ok and FakeEditor.instances[0].closed


def test_editor_that_will_not_close_is_a_stop(fake):
    fake["rows"], fake["fields"], fake["close_error"] = VAT_ROWS, VAT_FIELDS, "did not close (dialogs: ['Save'])"
    out = md.check_vat(MAIN, REG, Decimal("19"), ocr=None)
    assert out.error and "did not close" in out.summary() and not out.ok


def test_unreadable_view_is_a_stop(fake):
    fake["read_error"] = "VATs: grid header reads ..."
    out = md.check_vat(MAIN, REG, Decimal("19"), ocr=None)
    assert out.error and not out.ok


# --- commands -------------------------------------------------------------------------------


def run(capsys, **kw):
    calls = []

    def vat(main, reg, rate, ocr):
        calls.append(("vat", rate))
        return MasterCheck(f"VAT {rate}%", "reusable", "ok")

    def pay(main, reg, method, ocr):
        calls.append(("payment", method))
        return MasterCheck(f"Payment {method.value}", "missing", "none")

    code = cmd_check_master(registry=REG, finder=lambda: MAIN, vat_checker=vat, payment_checker=pay, ocr=object(), **kw)
    return code, calls, capsys.readouterr()


def test_check_master_from_the_sample_spec(capsys):
    code, calls, out = run(capsys, spec_path=FIX.parent / "golden" / "order_001.json")
    assert code == 0
    assert calls == [("vat", Decimal("19")), ("payment", PaymentMethod.BANK_TRANSFER)]  # one VAT rate for both lines
    assert "Payment Bank Transfer  missing" in out.out and "Nothing was changed." in out.out


def test_check_vat_and_payment_arguments(capsys):
    assert run(capsys, rates=["19"])[1] == [("vat", Decimal("19"))]
    assert run(capsys, method="Credit Card")[1] == [("payment", PaymentMethod.CREDIT_CARD)]


def test_unknown_method_is_refused(capsys):
    code, calls, out = run(capsys, method="Cash")
    assert code == 1 and calls == [] and "known methods" in out.err
