"""Clipboard TSV parsing and the reader's decisions, with a fake backend (no Fakturama)."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fakturama_agent.extract.ocr import OcrResult
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.clipboard import ClipboardText
from fakturama_agent.uia.grid import analyze
from fakturama_agent.uia.commands import cmd_read_table
from fakturama_agent.uia.locators import Registry, TableSpec
from fakturama_agent.uia.tables import TableError, parse_tsv, read_table

FIX = Path(__file__).parent / "fixtures" / "tables"
PID = 100
REGISTRY = Registry.load()


def copied(name: str) -> str:
    return (FIX / f"copy_{name}.txt").read_bytes().decode("utf-8")


# --- parsing ---------------------------------------------------------------------


def test_documents_copy():
    rows = parse_tsv(copied("documents"), REGISTRY.table("documents"))
    assert [r["Document"] for r in rows] == ["PO000001", "PO000002", "PO000003"]
    assert rows[2]["Cust.Ref."] == "PO-CREATE-001"
    assert rows[0]["State"] == "COMMAND_ORDER_PENDING" and rows[0]["Total"] == "39.87"
    assert rows[0]["icon"] == "ICON_ORDER" and rows[0]["Printed"] is None  # "null" -> None


def test_products_copy_has_model_values():
    rows = parse_tsv(copied("products"), REGISTRY.table("products"))
    assert [r["Item No."] for r in rows] == ["SKU-001", "SKU-002", "NEW-001", "NEW-002", "ORN-001", "ORN-002"]
    assert rows[0]["Price"] == "11.899999999999999"  # a Java double, not the displayed $11.90
    assert rows[0]["Stock"] is None
    assert rows[0]["VAT"].startswith("VAT taxValue: [0.19]")


@pytest.mark.parametrize("name, n", [("debtors", 3), ("vats", 2), ("payments", 1)])
def test_other_copies(name, n):
    rows = parse_tsv(copied(name), REGISTRY.table(name))
    assert len(rows) == n


def test_debtor_identity_columns():
    alice = parse_tsv(copied("debtors"), REGISTRY.table("debtors"))[0]
    assert alice == {"No.": "CUST000001", "First Name": "Alice", "Name": "Smith", "Company": "Learning Labs",
                     "ZIP": "10115", "City": "Berlin"}


@pytest.mark.parametrize(
    "text",
    [
        "a\tb\tc\r\n",  # too few cells
        "Cash\tPay\tCash\t0.0\t0\t0\textra\r\n",  # too many
        "true\tCash\tPay\nCash\t0.0\t0\t0",  # embedded line break -> wrong cell count
        "",
    ],
)
def test_malformed_copies_are_refused(text):
    with pytest.raises(TableError):
        parse_tsv(text, REGISTRY.table("payments"))


def test_trailing_line_break_is_optional():
    spec = REGISTRY.table("payments")
    assert parse_tsv(copied("payments"), spec) == parse_tsv(copied("payments") + "\r\n", spec)


# --- reader decisions ------------------------------------------------------------


def highlighted(image: Image.Image, rows: int) -> Image.Image:
    """The grid as Ctrl+A leaves it: used rows filled with the selection blue (as recorded)."""
    img = image.convert("RGB").copy()
    px = analyze(img)
    draw = ImageDraw.Draw(img)
    for i in range(rows):
        top, bottom = px.band(i)
        draw.rectangle([0, top, px.columns_width, bottom], fill=(0, 120, 212))
    return img


class FakeGrid:
    def __init__(self, name, *, copies=None, owner=PID, images=None, select_works=True):
        self.name = name
        self.images = list(images) if images else None
        self.copies = list(copies) if copies is not None else [copied(name)] * 2
        self.owner = owner
        self.copy_calls = 0
        self.shown = None
        self.all_selected = False
        self.select_works = select_works

    def show(self, spec: TableSpec) -> None:
        self.shown = spec

    def _plain(self) -> Image.Image:
        if self.images:
            return self.images.pop(0) if len(self.images) > 1 else self.images[0]
        return Image.open(FIX / f"grid_{self.name}.png")

    def capture(self) -> Image.Image:
        image = self._plain()
        return highlighted(image, analyze(image).ink_rows) if self.all_selected else image

    def select_all(self) -> None:
        self.all_selected = self.select_works

    def copy(self) -> ClipboardText:
        assert self.all_selected, "Copy without a visible selection crashes Fakturama"
        self.copy_calls += 1
        return ClipboardText(sequence=self.copy_calls, owner_pid=self.owner, text=self.copies.pop(0))


def recorded_ocr(name):
    result = OcrResult.load(FIX / f"header_{name}.ocr.json")
    return lambda _png: result


def read(name, backend, ocr_name=None):
    return read_table(name, REGISTRY.table(name), backend, fakturama_pid=PID, ocr=recorded_ocr(ocr_name or name),
                      stable_timeout=1.0, interval=0.01)


def test_reads_documents():
    backend = FakeGrid("documents")
    result = read("documents", backend)
    assert backend.shown.filter == "Orders"
    assert backend.copy_calls == 2
    assert [r["Cust.Ref."] for r in result.rows] == ["PO-001", "PO-NEW-001", "PO-CREATE-001"]


def test_empty_grid_is_never_copied():
    backend = FakeGrid("creditors", copies=[])
    result = read("creditors", backend)
    assert result.rows == [] and backend.copy_calls == 0


def test_wrong_view_header_is_refused_before_copying():
    backend = FakeGrid("products")
    with pytest.raises(TableError, match="header"):
        read("products", backend, ocr_name="debtors")
    assert backend.copy_calls == 0


def test_clipboard_from_another_process_is_refused():
    with pytest.raises(TableError, match="not Fakturama"):
        read("vats", FakeGrid("vats", owner=999))


def test_two_different_copies_are_refused():
    first = copied("documents")
    with pytest.raises(TableError, match="differ"):
        read("documents", FakeGrid("documents", copies=[first, first.replace("PO-001", "PO-002")]))


def test_row_count_must_match_the_pixels():
    one_row_less = copied("documents").split("\r\n", 1)[1]
    with pytest.raises(TableError, match="grid shows 3"):
        read("documents", FakeGrid("documents", copies=[one_row_less] * 2))


def test_no_highlight_after_select_all_means_no_copy():
    backend = FakeGrid("documents", select_works=False)
    with pytest.raises(TableError, match="highlighted rows"):
        read("documents", backend)
    assert backend.copy_calls == 0


def test_recorded_select_all_highlight():
    from fakturama_agent.uia.grid import selected_bands
    from fakturama_agent.uia.tables import require_selection

    image = Image.open(FIX.parent / "selectors" / "products_view_select_all.png")
    require_selection(image, [0, 1, 2, 3, 4, 5])
    assert selected_bands(image, analyze(image)) == [0, 1, 2, 3, 4, 5]


def test_grid_that_keeps_changing_is_refused():
    a, b = Image.open(FIX / "grid_vats.png"), Image.open(FIX / "grid_payments.png")
    with pytest.raises(TableError, match="kept changing"):
        read("vats", FakeGrid("vats", images=[a, b] * 500))


def test_stable_after_a_change_is_read():
    a, b = Image.open(FIX / "grid_payments.png"), Image.open(FIX / "grid_vats.png")
    assert len(read("vats", FakeGrid("vats", images=[a, b, b, b])).rows) == 2


# --- command ---------------------------------------------------------------------

MAIN = MainWindow(hwnd=3, pid=PID, title="Fakturama", workspace=None, minimized=False)


def test_cmd_read_table(tmp_path, capsys):
    out = tmp_path / "docs.json"
    code = cmd_read_table("documents", out, registry=REGISTRY, finder=lambda: MAIN,
                          backend_factory=lambda m, r: FakeGrid("documents"), ocr=recorded_ocr("documents"))
    printed = capsys.readouterr().out
    assert code == 0
    assert "Documents > Orders: 3 row(s)" in printed and "PO-CREATE-001" in printed
    assert '"Cust.Ref.": "PO-001"' in out.read_text(encoding="utf-8")


def test_cmd_read_table_empty_and_failure(capsys):
    assert cmd_read_table("creditors", registry=REGISTRY, finder=lambda: MAIN,
                          backend_factory=lambda m, r: FakeGrid("creditors", copies=[]),
                          ocr=recorded_ocr("creditors")) == 0
    assert "not copied" in capsys.readouterr().out
    assert cmd_read_table("vats", registry=REGISTRY, finder=lambda: MAIN,
                          backend_factory=lambda m, r: FakeGrid("vats", owner=1), ocr=recorded_ocr("vats")) == 2
    assert "Cannot read VATs" in capsys.readouterr().err
