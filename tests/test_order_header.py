"""Order header: read-back from the recorded editor, comparison, typing rules, command."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from fakturama_agent.flow.order_header import HeaderError, HeaderRead, HeaderWanted, compare, read_header
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_order_open
from fakturama_agent.uia.inputs import InputError, escape_keys, list_items
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.tree import Snapshot

FIX = Path(__file__).parent / "fixtures" / "uia"
GOLDEN = Path(__file__).parent / "golden" / "order_001.json"
REGISTRY = Registry.load()
WANTED = HeaderWanted(cust_ref="WEB-2026-0714-A17", order_date=date(2026, 7, 14))
GOOD = HeaderRead(number="PO000004", date_shown="Jul 14, 2026", cust_ref="WEB-2026-0714-A17",
                  price_mode="Net", vat_mode="With VAT")


def test_read_header_from_the_recorded_editor():
    # recorded after the live header entry (2026-11-05)
    assert read_header(Snapshot.load(FIX / "order_editor.json"), REGISTRY) == GOOD


def test_open_vat_dropdown_lists_its_entries():
    snap = Snapshot.load(FIX / "order_editor_vat_open.json")
    combo = REGISTRY.resolve(snap, "order.vat_mode")
    assert [(n.name, n.selected) for n in list_items(combo)] == [("With VAT", True), ("Free of Tax", False)]
    assert list_items(REGISTRY.resolve(Snapshot.load(FIX / "order_editor.json"), "order.vat_mode")) == []


def test_compare_all_ok():
    assert all(c.ok for c in compare(WANTED, GOOD))


@pytest.mark.parametrize(
    "change, field",
    [
        ({"date_shown": "Jul 14, 2025"}, "Date"),
        ({"date_shown": "Nov 5, 2026"}, "Date"),
        ({"cust_ref": "WEB-2026-0714-A1"}, "Cust.Ref."),
        ({"cust_ref": "web-2026-0714-a17"}, "Cust.Ref."),  # exact, not case-folded
        ({"price_mode": "Gross"}, "Net/Gross"),
        ({"vat_mode": "Free of Tax"}, "VAT"),
        ({"number": ""}, "No."),
    ],
)
def test_compare_flags_the_wrong_field(change, field):
    failed = [c.field for c in compare(WANTED, replace(GOOD, **change)) if not c.ok]
    assert failed == [field]


def test_escape_keys():
    assert escape_keys("WEB-2026-0714-A17") == "WEB-2026-0714-A17"
    assert escape_keys("A+B (x) {y} 50%~^[z]") == "A{+}B {(}x{)} {{}y{}} 50{%}{~}{^}{[}z{]}"
    assert escape_keys("Müller & Söhne") == "Müller & Söhne"
    for bad in ("a\nb", "a\tb", "a\rb"):
        with pytest.raises(InputError):
            escape_keys(bad)


# --- command with a fake editor -------------------------------------------------------

MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama", workspace=None, minimized=False)


class FakeEditor:
    def __init__(self, result: HeaderRead | Exception):
        self.result = result
        self.wanted = None

    def read(self) -> HeaderRead:
        return replace(GOOD, date_shown="Nov 5, 2026", cust_ref="", price_mode="Gross")

    def set_header(self, wanted):
        self.wanted = wanted
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run(tmp_path, result, spec=GOLDEN):
    editor = FakeEditor(result)
    code = cmd_order_open(spec, registry=REGISTRY, finder=lambda: MAIN, editor_factory=lambda m, r: editor)
    return code, editor


def test_cmd_order_open_ok(tmp_path, capsys):
    code, editor = run(tmp_path, GOOD)
    out = capsys.readouterr().out
    assert code == 0
    assert editor.wanted == WANTED  # taken from the golden OrderSpec
    assert "Header set and verified. Nothing was saved." in out and "[ok  ] Date" in out


def test_cmd_order_open_mismatch_exit_2(tmp_path, capsys):
    code, _ = run(tmp_path, replace(GOOD, price_mode="Gross"))
    out = capsys.readouterr().out
    assert code == 2 and "[FAIL] Net/Gross" in out and "NOT as wanted" in out


def test_cmd_order_open_stops_on_errors(tmp_path, capsys):
    code, _ = run(tmp_path, HeaderError("the bound New Order editor is not visible"))
    err = capsys.readouterr().err
    assert code == 2 and "Stopped: the bound New Order editor" in err and "answer No" in err


def test_cmd_order_open_bad_spec(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"order": {}}), encoding="utf-8")
    code, editor = run(tmp_path, GOOD, spec=bad)
    assert code == 1 and editor.wanted is None
