"""Empty-grid detection and header check on grids captured live (2026-11-05)."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fakturama_agent.extract.ocr import OcrResult
from fakturama_agent.uia.grid import GRID_LINE, GridUnreadable, analyze, check_header, read_header, selected_bands
from fakturama_agent.uia.locators import Registry

FIX = Path(__file__).parent / "fixtures" / "tables"


def grid(name: str) -> Image.Image:
    return Image.open(FIX / f"grid_{name}.png")


@pytest.mark.parametrize(
    "name, rows",
    [("creditors", 0), ("debtors", 3), ("documents", 3), ("products", 6), ("vats", 2), ("payments", 1)],
)
def test_rows_seen_in_recorded_grids(name, rows):
    px = analyze(grid(name))
    assert (px.header_bottom, px.pitch, px.bands) == (19, 20, 15)
    assert px.columns_width >= 400  # header cells, not the full-width grid lines
    assert px.ink_rows == rows
    assert px.empty == (rows == 0)
    assert not px.full


def test_selected_row_and_grey_header_documents():
    # Documents was captured with PO000003 selected (blue) and a grey header band
    assert analyze(grid("documents")).ink_rows == 3


def synthetic(rows_with_text: int, *, bands: int = 10, pitch: int = 20, width: int = 400) -> Image.Image:
    img = Image.new("RGB", (width, 20 + bands * pitch), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 300, 18], fill=(240, 240, 240))  # header cells, as in the views
    d.text((5, 4), "Item No.", fill="black")
    for y in [19 + i * pitch for i in range(bands + 1)]:
        d.line([(0, y), (width, y)], fill=GRID_LINE)
    for x in (100, 200):
        d.line([(x, 0), (x, img.height)], fill=GRID_LINE)
    for i in range(rows_with_text):
        d.text((5, 22 + i * pitch), f"ROW-{i}", fill="black")
    return img


def test_full_grid_means_more_rows_may_exist():
    px = analyze(synthetic(10))
    assert px.full and px.ink_rows == 10


def test_text_after_an_empty_row_is_unreadable():
    img = synthetic(2)
    ImageDraw.Draw(img).text((5, 22 + 5 * 20), "POPUP", fill="black")  # e.g. a tooltip over the grid
    with pytest.raises(GridUnreadable, match="after an empty row"):
        analyze(img)


@pytest.mark.parametrize(
    "image",
    [
        Image.new("RGB", (600, 300), "white"),  # blank: no grid lines -> not "empty", unreadable
        Image.new("RGB", (600, 300), (30, 30, 30)),  # dark/covered
        Image.new("RGB", (20, 20), "white"),  # too small
    ],
)
def test_unrecognised_pictures_are_never_empty(image):
    with pytest.raises(GridUnreadable):
        analyze(image)


@pytest.mark.parametrize("name", ["creditors", "debtors", "documents", "products", "vats", "payments"])
def test_recorded_headers_match_the_registry(name):
    spec = Registry.load().table(name)
    ocr = OcrResult.load(FIX / f"header_{name}.ocr.json")
    image = grid(name)
    check_header(read_header(image, analyze(image), lambda _png: ocr), spec.columns)


def test_header_mismatch():
    with pytest.raises(GridUnreadable, match="expected"):
        check_header(["Name", "Item No."], ["Item No.", "Name"])  # columns swapped
    with pytest.raises(GridUnreadable):
        check_header(["Item No.", "Name"], ["Item No.", "Name", "Price"])  # a column hidden
    check_header(["Total ", "Disc.Days"], ["Total", "Disc. Days"])  # spaces ignored
    check_header(["Document"], ["", "Document"])  # header-less icon column


# --- dialog style (Select the address) and selections, recorded 2026-11-05 ---------------

SEL = FIX.parent / "selectors"


def sel(name: str) -> Image.Image:
    return Image.open(SEL / f"{name}.png")


@pytest.mark.parametrize(
    "name, rows, selected",
    [
        ("address_all", 3, []),  # bordered header, row numbers, alternating shading, lines only as wide as the table
        ("address_one", 1, []),  # after a search: nothing selected
        ("address_one_selected", 1, [0]),  # light-blue selection, grey header
        ("address_none", 0, []),  # 'Northstar Office GmbH': header only, white below
        ("product_all", 6, []),
        ("product_one", 1, []),
        ("product_one_selected", 1, [0]),  # strong blue selection
        ("order_items_one", 1, [0]),  # the Order's items: a new line arrives selected
        ("products_view_select_all", 6, [0, 1, 2, 3, 4, 5]),  # Ctrl+A in a Data view
    ],
)
def test_recorded_selector_grids(name, rows, selected):
    image = sel(name)
    px = analyze(image)
    assert (px.header_bottom, px.pitch) == (19, 20)
    assert px.ink_rows == rows
    assert selected_bands(image, px) == selected


def test_selection_only_as_wide_as_the_columns():
    # live failure 2026-11-05: in a 1872-px pane the selected row is blue only under the
    # columns (~600 px); measuring against the full width missed it
    narrow = sel("product_one_selected").convert("RGB")
    wide = Image.new("RGB", (1872, narrow.height), "white")
    wide.paste(narrow, (0, 0))
    edge = narrow.crop((narrow.width - 1, 0, narrow.width, narrow.height))
    for x in range(narrow.width, wide.width):
        wide.paste(edge, (x, 0))
    px = analyze(wide)
    assert px.table_width == 1872 and px.columns_width == 600
    assert selected_bands(wide, px) == [0]


def test_documents_grey_header_band_and_selected_row():
    image = grid("documents")
    assert selected_bands(image, analyze(image)) == [2]  # PO000003 was selected when recorded


@pytest.mark.parametrize(
    "name, table",
    [("address_all", "select_address"), ("address_one_selected", "select_address"),
     ("product_all", "select_product"), ("order_items_one", "order_items")],
)
def test_selector_headers(name, table):
    spec = Registry.load().table(table)
    ocr = OcrResult.load(SEL / f"{name}.header.ocr.json")
    image = sel(name)
    check_header(read_header(image, analyze(image), lambda _png: ocr), spec.expected_header(),
                 prefix=spec.checked is not None)


def test_click_point_is_inside_the_first_data_column():
    px = analyze(sel("address_one"))
    x, y = px.click_point(0)
    assert 40 < x <= 80 and px.band(0)[0] <= y <= px.band(0)[1]  # past the row-number column
