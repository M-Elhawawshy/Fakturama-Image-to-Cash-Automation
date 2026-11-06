"""Phase 4 part B: the linked Invoice's copied header, the visible grid area, invoice-open."""

from pathlib import Path

import pytest

from fakturama_agent.flow.create_order import Step
from fakturama_agent.flow.invoice import InvoiceHeader, InvoiceRun, header_problems, read_invoice_header
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_invoice_open
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat
from fakturama_agent.uia.tree import Snapshot, UiNode, visible_rect

FIX = Path(__file__).parent / "fixtures"
GOLDEN = FIX.parent / "golden" / "order_001.json"
SPEC = OrderSpec.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
REG = Registry.load()


class _Editor:  # read_invoice_header needs .snapshot() and .registry
    def __init__(self, snap):
        self._snap, self.registry = snap, REG

    def snapshot(self):
        return self._snap


RECORDED = read_invoice_header(_Editor(Snapshot.load(FIX / "uia" / "invoice_editor.json")))


def test_recorded_header_is_a_correct_copy():
    assert RECORDED == InvoiceHeader("INV000001", "Nov 6, 2026", "Nov 6, 2026", "Jul 14, 2026",
                                     "WEB-2026-0714-A17", "Net", "With VAT")
    assert header_problems(RECORDED, SPEC) == []


@pytest.mark.parametrize(
    "change, needle",
    [({"number": "PO000006"}, "No."), ({"number": ""}, "No."), ({"date": "<choose date>"}, "Invoice Date"),
     ({"service_date": ""}, "Service date"), ({"order_date": "Nov 6, 2026"}, "Order Date"),
     ({"cust_ref": "WEB-X"}, "Cust.Ref."), ({"price_mode": "Gross"}, "price mode"),
     ({"vat_mode": "Free of Tax"}, "VAT")],
)
def test_header_differences(change, needle):
    problems = header_problems(InvoiceHeader(**{**RECORDED.__dict__, **change}), SPEC)
    assert len(problems) == 1 and needle in problems[0]


def test_proposed_dates_are_not_compared_with_the_order_date():
    other_day = InvoiceHeader(**{**RECORDED.__dict__, "date": "Dec 1, 2026", "service_date": "Dec 1, 2026"})
    assert header_problems(other_day, SPEC) == []  # 5.1: proposed, left unchanged


# --- visible part of a grid in a scrolled editor ----------------------------------------------------


def _chain(*rects):
    """Innermost first: node, parent, grandparent ..."""
    nodes = [UiNode(control_type="Pane", name="", rect=r) for r in rects]
    for child, parent in zip(nodes, nodes[1:]):
        child.parent = parent
    return nodes[0]


def test_visible_rect_clips_to_the_scroll_viewport():
    # recorded on the Invoice: grid (427,375,1892,514) inside a Scroll pane ending at y=467
    grid = _chain((427, 375, 1892, 514), (425, 373, 1900, 516), (390, 141, 1900, 516), (390, 141, 1917, 467),
                  (390, 141, 1917, 634))
    assert visible_rect(grid) == (427, 375, 1892, 467)


def test_visible_rect_refuses_a_hidden_top_edge():
    with pytest.raises(ValueError):
        visible_rect(_chain((427, 100, 1892, 300), (390, 141, 1917, 634)))


# --- command -----------------------------------------------------------------------------------------

MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama", workspace=None, minimized=False)


class Report:
    ok, checks = True, []
    money = MoneyFormat.from_example("$-1,234.57", 2)


def run_cmd(capsys, run):
    code = cmd_invoice_open(GOLDEN, registry=REG, finder=lambda: MAIN, preflight=lambda m, r: Report(),
                            opener=lambda *a: run, ocr=object())
    return code, capsys.readouterr().out


def test_invoice_open_all_ok_leaves_the_invoice_unsaved(capsys):
    code, out = run_cmd(capsys, InvoiceRun([Step("Invoice header", True, "fine")], "PO000006", invoice=object()))
    assert code == 0 and "Nothing was saved. The New Invoice is left open" in out


def test_invoice_open_failure_before_the_invoice_exists(capsys):
    code, out = run_cmd(capsys, InvoiceRun([Step("linked Invoice", False, "the saved Order is not verified")]))
    assert code == 2 and "[FAIL] linked Invoice" in out and "left open" not in out


# --- part C: paid status rules -------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from fakturama_agent.flow.invoice import paid_problems  # noqa: E402
from fakturama_agent.spec.models import PaidStatus  # noqa: E402

TOTAL = Decimal("678.30")
UNPAID = SPEC.model_copy(update={"payment": SPEC.payment.model_copy(
    update={"paid_status": PaidStatus.UNPAID, "payment_date": None})})


def test_paid_as_recorded_live():
    assert paid_problems("on", "Jul 18, 2026", TOTAL, TOTAL, SPEC) == []  # read back 2026-11-06


@pytest.mark.parametrize(
    "paid, shown, value, total, needle",
    [("off", "Jul 18, 2026", TOTAL, TOTAL, "paid is 'off'"),
     ("on", "Nov 6, 2026", TOTAL, TOTAL, "paid date"),
     ("on", "Jul 18, 2026", Decimal("600.00"), TOTAL, "Value"),
     ("on", "Jul 18, 2026", None, TOTAL, "Value"),
     ("on", "Jul 18, 2026", Decimal("620.00"), Decimal("620.00"), "Invoice Total")],
)
def test_paid_differences(paid, shown, value, total, needle):
    problems = paid_problems(paid, shown, value, total, SPEC)
    assert len(problems) == 1 and needle in problems[0]


def test_unpaid_leaves_paid_clear():
    assert paid_problems("off", "", None, None, UNPAID) == []
    assert "UNPAID" in paid_problems("on", "", None, None, UNPAID)[0]


# --- part D: the saved Invoice in Documents ----------------------------------------------------------

from fakturama_agent.flow.invoice import PAID_STATE, invoice_row_problems  # noqa: E402
from fakturama_agent.uia.tables import parse_tsv  # noqa: E402

INVOICES = parse_tsv((FIX / "tables" / "copy_invoices.txt").read_bytes().decode(), REG.table("invoices"))
CONTACT = "Northstar Office GmbH, Marta Klein"


def test_recorded_invoice_row():
    assert INVOICES[0]["Document"] == "INV000001" and INVOICES[0]["State"] == PAID_STATE
    assert invoice_row_problems(INVOICES, "INV000001", SPEC, CONTACT) == []


@pytest.mark.parametrize(
    "change, needle",
    [({"Total": "620.0"}, "Total"), ({"State": "COMMAND_ERROR"}, "State"), ({"Name": "Other"}, "Name"),
     ({"Cust.Ref.": "WEB-X"}, "Cust.Ref.")],
)
def test_invoice_row_differences(change, needle):
    problems = invoice_row_problems([{**INVOICES[0], **change}], "INV000001", SPEC, CONTACT)
    assert problems and needle in problems[0]


def test_unpaid_invoice_must_not_show_paid():
    assert "UNPAID" in invoice_row_problems(INVOICES, "INV000001", UNPAID, CONTACT)[0]
    assert invoice_row_problems([{**INVOICES[0], "State": "COMMAND_ERROR"}], "INV000001", UNPAID, CONTACT) == []


def test_missing_or_duplicated_invoice():
    assert "0 Invoices rows" in invoice_row_problems(INVOICES, "INV000002", SPEC, CONTACT)[0]
    two = [*INVOICES, {**INVOICES[0], "Document": "INV000002"}]
    assert any("2 Invoices carry" in p for p in invoice_row_problems(two, "INV000001", SPEC, CONTACT))


def test_invoice_date_is_not_compared_with_the_order_date():
    assert "Nov 06" in INVOICES[0]["Date"]  # the Invoice Date (proposed, today), not Jul 14


# --- commands ----------------------------------------------------------------------------------------------

from fakturama_agent.uia.commands import cmd_check_invoice, cmd_create_invoice  # noqa: E402


def _create(capsys, run, **kw):
    seen = {}

    def creator(main, registry, spec, money, ocr, **flags):
        seen.update(flags)
        return run

    code = cmd_create_invoice(GOLDEN, registry=REG, finder=lambda: MAIN, preflight=lambda m, r: Report(),
                              creator=creator, ocr=object(), **kw)
    return code, seen, capsys.readouterr().out


def test_create_invoice_outcomes(capsys):
    done = InvoiceRun([Step("save Invoice", True, "saved once: INV000001")], saved=True, number="INV000001")
    code, seen, out = _create(capsys, done)
    assert code == 0 and seen == {"allow_duplicate": False, "save": True} and "INV000001 saved and verified" in out
    unclear = InvoiceRun([Step("save Invoice", False, "outcome unclear")], saved=True)
    assert "do not rerun blindly" in _create(capsys, unclear)[2]
    guard = InvoiceRun([Step("invoice guard", False, "already carry")])
    code, _, out = _create(capsys, guard)
    assert code == 2 and "Nothing was saved." in out
    code, seen, out = _create(capsys, InvoiceRun([Step("save Invoice", True, "skipped")], invoice=object()),
                              save=False, allow_duplicate=True)
    assert code == 0 and seen == {"allow_duplicate": True, "save": False} and "--no-save" in out


def test_check_invoice_exit_code(capsys):
    ok = cmd_check_invoice(GOLDEN, registry=REG, finder=lambda: MAIN, preflight=lambda m, r: Report(),
                           verifier=lambda *a: [Step("Invoice in Documents", True, "fine")], ocr=object())
    bad = cmd_check_invoice(GOLDEN, registry=REG, finder=lambda: MAIN, preflight=lambda m, r: Report(),
                            verifier=lambda *a: [Step("source Order", False, "not open")], ocr=object())
    assert (ok, bad) == (0, 2)
