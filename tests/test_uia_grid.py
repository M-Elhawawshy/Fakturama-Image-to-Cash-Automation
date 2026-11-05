"""Empty-grid detection and header check on grids captured live (2026-11-05)."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fakturama_agent.extract.ocr import OcrResult
from fakturama_agent.uia.grid import GRID_LINE, GridUnreadable, analyze, check_header, read_header
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
    assert px.ink_rows == rows
    assert px.empty == (rows == 0)
    assert not px.full


def test_selected_row_and_grey_header_documents():
    # Documents was captured with PO000003 selected (blue) and a grey header band
    assert analyze(grid("documents")).ink_rows == 3


def synthetic(rows_with_text: int, *, bands: int = 10, pitch: int = 20, width: int = 400) -> Image.Image:
    img = Image.new("RGB", (width, 20 + bands * pitch), "white")
    d = ImageDraw.Draw(img)
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
