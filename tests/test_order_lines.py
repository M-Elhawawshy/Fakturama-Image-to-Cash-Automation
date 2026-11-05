"""Part I: line edits and checks, footer, Documents row, grid columns, the create-order command."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from fakturama_agent.flow.create_order import OrderRun
from fakturama_agent.flow.order_lines import (
    Footer,
    documents_problems,
    duplicates,
    footer_problems,
    java_date,
    line_edits,
    lines_problems,
    model_amount,
    plain,
)
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_create_order
from fakturama_agent.uia.grid import GridUnreadable, analyze, cell_point, column_edges
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat
from fakturama_agent.uia.tables import parse_tsv

FIX = Path(__file__).parent / "fixtures"
GOLDEN = FIX.parent / "golden" / "order_001.json"
SPEC = OrderSpec.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
REG = Registry.load()
US = MoneyFormat.from_example("$-1,234.57", 2)
VAT = ("VAT taxValue: [0.19] salesEqualizationTax: [null] description: [VAT 19%] name: [VAT 19%] "
       "dateAdded: [Tue Nov 03 00:00:00 EET 2026]")

# copied from the live Order on 2026-11-05: right after selecting the products, then after the edits
FRESH = [
    {"Qty.": "1.00", "Item No.": "CHR-ERG-01", "Picture": None, "Name": "Ergonomic Desk Chair", "Description": "",
     "VAT": VAT, "U.Price": "USD 250", "Discount": "0.0", "Price": "USD 250"},
    {"Qty.": "1.00", "Item No.": "MAT-DESK-02", "Picture": None, "Name": "Anti-Fatigue Desk Mat", "Description": "",
     "VAT": VAT, "U.Price": "USD 40", "Discount": "0.0", "Price": "USD 40"},
]
EDITED = [
    {**FRESH[0], "Qty.": "2.00", "Discount": "-0.1", "Price": "USD 450"},
    {**FRESH[1], "Qty.": "3.00", "Price": "USD 120"},
]


def test_model_amounts():
    assert model_amount("USD 450") == Decimal("450.00")
    assert model_amount("678.3") == Decimal("678.30")
    assert model_amount("11.899999999999999") == Decimal("11.90")
    assert model_amount("null") is None and model_amount(None) is None


def test_plain_numbers_typed_into_cells():
    assert plain(Decimal("2")) == "2" and plain(Decimal("10")) == "10" and plain(Decimal("2.50")) == "2.5"
    assert plain(Decimal("1E+1")) == "10"


def test_only_differing_cells_are_typed():
    assert line_edits(FRESH[0], SPEC.items[0]) == [("Qty.", "2"), ("Discount", "10")]
    assert line_edits(FRESH[1], SPEC.items[1]) == [("Qty.", "3")]
    assert line_edits(EDITED[0], SPEC.items[0]) == [] and line_edits(EDITED[1], SPEC.items[1]) == []
    other_price = {**FRESH[1], "U.Price": "USD 41.5"}
    assert ("U.Price", "40.00") in line_edits(other_price, SPEC.items[1])


def test_edited_lines_match_the_spec():
    assert lines_problems(EDITED, SPEC) == []


@pytest.mark.parametrize(
    "line, change, needle",
    [(0, {"Qty.": "1.00"}, "Qty."), (0, {"Discount": "0.1"}, "Discount"), (0, {"Price": "USD 449.99"}, "Price"),
     (1, {"U.Price": "USD 41"}, "U.Price"), (1, {"VAT": VAT.replace("VAT 19%", "VAT 7%")}, "VAT")],
)
def test_line_differences_are_named(line, change, needle):
    rows = [dict(r) for r in EDITED]
    rows[line].update(change)
    problems = lines_problems(rows, SPEC)
    assert len(problems) == 1 and f"line {line + 1}" in problems[0] and needle in problems[0]


def test_wrong_or_reordered_lines():
    assert "lines are" in lines_problems(EDITED[::-1], SPEC)[0]
    assert "lines are" in lines_problems(EDITED[:1], SPEC)[0]


FOOTER = Footer("$570.00", "$108.30", "$678.30", "0%", "Free of shipping costs", "$0.00")  # recorded


def test_footer_matches():
    assert footer_problems(FOOTER, SPEC, US) == []


@pytest.mark.parametrize(
    "change, needle",
    [({"total_net": "$620.00"}, "Total Net"), ({"vat": "$117.80"}, "VAT"), ({"total": "$678.31"}, "Total"),
     ({"discount": "5%"}, "Discount"), ({"shipping_amount": "$4.90"}, "Shipping"), ({"shipping": "DHL"}, "Shipping")],
)
def test_footer_differences(change, needle):
    f = Footer(**{**FOOTER.__dict__, **change})
    problems = footer_problems(f, SPEC, US)
    assert len(problems) == 1 and needle in problems[0]


def test_java_dates():
    assert java_date("Tue Jul 14 00:00:00 EEST 2026") == date(2026, 7, 14)
    assert java_date("Tue Nov 03 00:00:00 EET 2026") == date(2026, 11, 3)
    assert java_date("Jul 14, 2026") is None


DOCS = parse_tsv((FIX / "tables" / "copy_documents.txt").read_bytes().decode(), REG.table("documents"))
SAVED = {"": "ICON_ORDER", "Document": "PO000004", "Date": "Tue Jul 14 00:00:00 EEST 2026",
         "Name": "Northstar Office GmbH, Marta Klein", "Cust.Ref.": "WEB-2026-0714-A17",
         "State": "COMMAND_ORDER_PENDING", "Total": "678.3", "Printed": None}
CONTACT = "Northstar Office GmbH, Marta Klein"


def test_documents_row_of_the_saved_order():
    assert documents_problems([*DOCS, SAVED], "PO000004", SPEC, CONTACT) == []


@pytest.mark.parametrize(
    "change, needle",
    [({"Total": "620.0"}, "Total"), ({"State": "COMMAND_ORDER_SHIPPED"}, "State"),
     ({"Date": "Thu Nov 05 00:00:00 EET 2026"}, "Date"), ({"Name": "Northstar Office GmbH"}, "Name"),
     ({"Cust.Ref.": "WEB-X"}, "Cust.Ref.")],
)
def test_documents_differences(change, needle):
    problems = documents_problems([*DOCS, {**SAVED, **change}], "PO000004", SPEC, CONTACT)
    assert problems and needle in problems[0]


def test_documents_missing_or_duplicated():
    assert "0 Documents rows" in documents_problems(DOCS, "PO000004", SPEC, CONTACT)[0]
    twice = [*DOCS, SAVED, {**SAVED, "Document": "PO000005"}]
    assert any("2 orders carry" in p for p in documents_problems(twice, "PO000004", SPEC, CONTACT))


def test_duplicate_guard_rows():
    assert duplicates(DOCS, "WEB-2026-0714-A17") == []
    assert duplicates([*DOCS, SAVED], "WEB-2026-0714-A17") == ["PO000004"]
    assert duplicates(DOCS, "PO-001") == ["PO000001"]


# --- grid columns ---------------------------------------------------------------------------------

EDGES = [39, 139, 239, 339, 439, 539, 639, 739, 839, 939]


@pytest.mark.parametrize("name", ["order_items_one", "order_items_two", "order_items_focused"])
def test_column_edges_with_and_without_header_separators(name):
    image = Image.open(FIX / "selectors" / f"{name}.png")
    assert column_edges(image, analyze(image)) == EDGES


def test_focused_header_needs_an_empty_row():
    image = Image.open(FIX / "selectors" / "order_items_focused.png")
    pixels = analyze(image)
    full = type(pixels)(**{**pixels.__dict__, "ink_rows": pixels.bands})
    with pytest.raises(GridUnreadable):
        column_edges(image, full)


def test_cell_points_are_the_ones_clicked_live():
    image = Image.open(FIX / "selectors" / "order_items_two.png")
    pixels = analyze(image)
    assert cell_point(EDGES, 1, pixels, 0) == (89, 29)  # Qty. of line 1
    assert cell_point(EDGES, 8, pixels, 0) == (789, 29)  # Discount of line 1
    assert cell_point(EDGES, 7, pixels, 1) == (689, 49)  # U.Price of line 2
    with pytest.raises(GridUnreadable):
        cell_point(EDGES, 10, pixels, 0)


# --- command ----------------------------------------------------------------------------------------

MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama", workspace=None, minimized=False)


class Report:
    ok, money, checks = True, US, []


def run_cmd(capsys, run: OrderRun, **kw):
    seen = {}

    def creator(main, registry, spec, money, ocr, **flags):
        seen.update(flags)
        return run

    code = cmd_create_order(GOLDEN, registry=REG, finder=lambda: MAIN, preflight=lambda m, r: Report(),
                            creator=creator, ocr=object(), **kw)
    return code, seen, capsys.readouterr().out


def _run(*steps, number=None, saved=False, left_open=False):
    r = OrderRun(number=number, saved=saved, unsaved_left_open=left_open)
    for name, ok in steps:
        r.add(name, [] if ok else ["bad"], "fine")
    return r


def test_saved_and_verified(capsys):
    code, seen, out = run_cmd(capsys, _run(("save", True), ("Documents", True), number="PO000004", saved=True))
    assert code == 0 and seen == {"allow_duplicate": False, "save": True} and "PO000004 saved and verified" in out


def test_stop_before_save_says_nothing_saved(capsys):
    code, _, out = run_cmd(capsys, _run(("lines", False), left_open=True))
    assert code == 2 and "Nothing was saved" in out and "Save Parts" in out


def test_unclear_save_warns(capsys):
    code, _, out = run_cmd(capsys, _run(("save", False), saved=True))
    assert code == 2 and "do not rerun blindly" in out


def test_no_save_and_override_flags(capsys):
    code, seen, out = run_cmd(capsys, _run(("save", True), left_open=True), save=False, allow_duplicate=True)
    assert code == 0 and seen == {"allow_duplicate": True, "save": False} and "nothing was saved" in out


# --- verify_documents (read-only check after Save, and check-order) ------------------------------------


@pytest.fixture
def docs(monkeypatch):
    import fakturama_agent.flow.create_order as co

    state = {"rows": [*DOCS, SAVED], "error": None}

    class Read:
        def __init__(self, rows):
            self.rows = rows

    def read_view(main, registry, key, ocr):
        if state["error"]:
            raise state["error"]
        return object(), Read(state["rows"])

    monkeypatch.setattr(co, "read_view", read_view)
    return state


def test_verify_finds_the_order_by_cust_ref(docs):
    from fakturama_agent.flow.create_order import verify_documents

    step = verify_documents(MAIN, REG, SPEC, ocr=None)
    assert step.ok and step.detail.startswith("PO000004")


def test_verify_never_crashes_after_a_save(docs):
    from fakturama_agent.flow.create_order import verify_documents

    docs["error"] = TimeoutError("timed out after 10s waiting for filter 'Orders' selected")
    step = verify_documents(MAIN, REG, SPEC, ocr=None, number="PO000004")
    assert not step.ok and "cannot read Documents" in step.detail


def test_verify_needs_exactly_one_order_for_the_ref(docs):
    from fakturama_agent.flow.create_order import verify_documents

    docs["rows"] = DOCS
    assert "0 Orders carry" in verify_documents(MAIN, REG, SPEC, ocr=None).detail
    docs["rows"] = [*DOCS, SAVED, {**SAVED, "Document": "PO000005"}]
    assert "2 Orders carry" in verify_documents(MAIN, REG, SPEC, ocr=None).detail


def test_cleared_addresses_are_named_plainly():
    from fakturama_agent.flow.order_lines import addresses_cleared

    assert addresses_cleared("Northstar Office GmbH\r\nMarta Klein") == []
    for empty in ("", "  ", None):
        assert "addresses were cleared" in addresses_cleared(empty)[0]
