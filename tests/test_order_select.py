"""Selector orchestration with fake dialogs and editor (no Fakturama)."""

import json
from pathlib import Path

import pytest

import fakturama_agent.flow.order_select as osel
from fakturama_agent.flow.match import Decision
from fakturama_agent.flow.order_select import Outcome, check_all, check_debtor, check_product, debtor_key
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_order_select
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.selector import SelectorError
from fakturama_agent.uia.tree import UiNode

ROOT = Path(__file__).parent
SAMPLE = OrderSpec.model_validate_json((ROOT / "golden" / "order_001.json").read_text(encoding="utf-8"))
EXISTING = OrderSpec.model_validate_json(
    (ROOT / "fixtures" / "specs" / "existing_debtor.json").read_text(encoding="utf-8")
)
ALICE_ROW = {"No.": "CUST000001", "First Name": "Alice", "Name": "Smith", "Company": "Learning Labs",
             "ZIP": "10115", "City": "Berlin", "address type": "BILLING,DELIVERY", "additional n...": None,
             "local consul...": None}
ALICE_ADDRESS = "Learning Labs\r\nAlice Smith\r\n10 Test Street\r\nDE-10115 Berlin\r\nGermany"
MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama", workspace=None, minimized=False)


class FakeDialog:
    log: list = []
    script: dict = {}  # search text -> rows, or an exception
    on_select = None

    def __init__(self, table_key):
        self.table_key = table_key
        self.open_ = True
        self.searched = None

    def search(self, text):
        self.searched = text
        FakeDialog.log.append(("search", self.table_key, text))

    def rows(self):
        result = FakeDialog.script[self.searched]
        if isinstance(result, Exception):
            raise result
        return result

    def select(self, index, expected):
        FakeDialog.log.append(("select", self.table_key, expected.get("No.") or expected.get("Item No.")))
        if FakeDialog.on_select:
            FakeDialog.on_select(expected)
        self.open_ = False

    def cancel(self):
        FakeDialog.log.append(("cancel", self.table_key))
        self.open_ = False

    def is_open(self):
        return self.open_


class FakeEditor:
    def __init__(self, address=""):
        self.main = MAIN
        self.registry = Registry.load()
        self.address = address

    def node(self, key):
        return UiNode("Edit", value=self.address) if key == "order.invoice_address" else UiNode("Image")


@pytest.fixture
def lines(monkeypatch):
    FakeDialog.log = []
    FakeDialog.script = {}
    FakeDialog.on_select = None
    monkeypatch.setattr(osel.SelectorDialog, "open",
                        classmethod(lambda cls, main, reg, icon, key, ocr: FakeDialog(key)))
    order_lines: list[dict] = []
    monkeypatch.setattr(osel, "read_items", lambda editor, ocr: list(order_lines))
    return order_lines


def test_debtor_key_from_spec():
    k = debtor_key(EXISTING)
    assert (k.company, k.first_name, k.last_name, k.zip, k.city) == ("Learning Labs", "Alice", "Smith", "10115", "Berlin")


def test_sample_nothing_exists(lines):
    FakeDialog.script = {"Northstar Office GmbH": [], "CHR-ERG-01": [], "MAT-DESK-02": []}
    outcomes = check_all(FakeEditor(), SAMPLE, ocr=None)
    assert [o.decision.kind for o in outcomes] == ["not_found"] * 3 and all(o.ok for o in outcomes)
    assert ("search", "select_address", "Northstar Office GmbH") in FakeDialog.log
    cancels = [e for e in FakeDialog.log if e[0] == "cancel"]
    assert cancels == [("cancel", "select_address"), ("cancel", "select_product"), ("cancel", "select_product")]


def test_existing_debtor_selected_and_verified(lines):
    FakeDialog.script = {"Learning Labs": [ALICE_ROW]}
    out = check_debtor(FakeEditor(ALICE_ADDRESS), EXISTING, ocr=None)
    assert out.ok and out.decision.kind == "select"
    assert ("select", "select_address", "CUST000001") in FakeDialog.log


def test_selected_debtor_not_on_the_order_fails(lines):
    FakeDialog.script = {"Learning Labs": [ALICE_ROW]}
    out = check_debtor(FakeEditor(""), EXISTING, ocr=None)
    assert not out.ok and out.problems and "ORDER CHECK FAILED" in out.summary()


def test_product_selection_must_add_one_line(lines):
    FakeDialog.script = {"SKU-001": [{"Item No.": "SKU-001"}]}
    FakeDialog.on_select = lambda row: lines.append({"Item No.": row["Item No."]})
    assert check_product(FakeEditor(), "SKU-001", ocr=None).ok
    FakeDialog.on_select = None  # OK pressed but no line appeared
    out = check_product(FakeEditor(), "SKU-001", ocr=None)
    assert not out.ok and "0 lines were added" in out.problems[0]


def test_conflict_cancels_and_needs_review(lines):
    FakeDialog.script = {"Learning Labs": [{**ALICE_ROW, "First Name": "Alicia"}]}
    out = check_debtor(FakeEditor(), EXISTING, ocr=None)
    assert out.decision.kind == "conflict" and not out.ok
    assert FakeDialog.log[-1] == ("cancel", "select_address")


def test_error_inside_the_dialog_still_cancels_and_stops_later_checks(lines):
    FakeDialog.script = {"Northstar Office GmbH": SelectorError("highlighted rows [], expected [0]; not copying")}
    outcomes = check_all(FakeEditor(), SAMPLE, ocr=None)
    assert len(outcomes) == 1 and "not copying" in outcomes[0].summary()
    assert FakeDialog.log[-1] == ("cancel", "select_address")


# --- command ----------------------------------------------------------------------------


def run_cmd(outcomes, spec=ROOT / "golden" / "order_001.json"):
    return cmd_order_select(spec, registry=Registry.load(), finder=lambda: MAIN,
                            editor_factory=lambda m, r: object(), checker=lambda e, s, o: outcomes, ocr=object())


def test_cmd_all_decided(capsys):
    outs = [Outcome("Debtor", Decision("not_found", reason="no contact")),
            Outcome("Item CHR-ERG-01", Decision("not_found", reason="none")),
            Outcome("Item MAT-DESK-02", Decision("not_found", reason="none"))]
    assert run_cmd(outs) == 0
    out = capsys.readouterr().out
    assert "[ok  ] Debtor" in out and "All checks decided. Nothing was saved." in out


def test_cmd_review_and_stop(capsys):
    outs = [Outcome("Debtor", Decision("ambiguous", reason="2 contacts")), Outcome("Item CHR-ERG-01", error="boom")]
    assert run_cmd(outs) == 2
    out = capsys.readouterr().out
    assert "[REVW] Debtor" in out and "[STOP] Item CHR-ERG-01" in out and "1 later check(s) not run" in out


def test_cmd_bad_spec(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({}), encoding="utf-8")
    assert run_cmd([], spec=bad) == 1
