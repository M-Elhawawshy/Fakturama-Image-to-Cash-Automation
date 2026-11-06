"""Annotated screenshots for the README, from a run's evidence folder (out/runs/<time>/).

The runner saves a screenshot of the Fakturama window before and after every step.
This script picks a few, blurs what is personal (the window title and the start
page show the workspace path), crops away screen corners (desktop notifications),
draws numbered boxes around what each step verified, and writes a caption panel.
Box positions are hand-placed for these documentation images only; the automation
itself never uses coordinates.

    python tools/annotate_screenshots.py --masters out/runs/<run that created master data> \
        --order out/runs/<run that saved Order and Invoice>
"""

from __future__ import annotations

import argparse
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
RED = (214, 39, 40)
TITLE_BAR = (0, 0, 1936, 32)  # window title: "Fakturama - <workspace path>"
START_URL = (400, 146, 1936, 172)  # start page address bar: file:///<workspace path>/...


@dataclass
class Shot:
    source: str  # file name inside the run folder
    out: str
    title: str
    boxes: list[tuple[tuple[int, int, int, int], str]]  # (left, top, right, bottom), note
    crop: tuple[int, int, int, int] | None = None
    blur: tuple[tuple[int, int, int, int], ...] = (TITLE_BAR,)


MASTERS = [
    Shot("03-product-chr-erg-01-after.png", "1-product-created.png",
         "Master data: product CHR-ERG-01 created (only because no exact Item No. existed)",
         [((426, 856, 1030, 878), "New row CHR-ERG-01: gross 297.50 = 250.00 net x 1.19, VAT 19% "
                                  "(selected before the price; read back in the editor before Save)"),
          ((426, 712, 1030, 734), "The Products list is read by Select All + Copy, not OCR; header checked by OCR")],
         crop=(0, 0, 1936, 1040), blur=(TITLE_BAR, START_URL)),
    Shot("05-debtor-northstar-office-gmbh-after.png", "2-debtor-created.png",
         "Master data: Debtor Northstar Office GmbH / Marta Klein created with billing + delivery address",
         [((426, 796, 1030, 818), "New contact (Customer ID proposed by Fakturama, kept). The list shows the "
                                  "DELIVERY ZIP 10553; the billing address is 10117"),
          ((426, 712, 1030, 734), "Identity check: exact Company, First Name, Name, ZIP, City")],
         crop=(0, 0, 1620, 1040), blur=(TITLE_BAR, START_URL)),
]
ORDER = [
    Shot("07-invoice-web-2026-0714-a17-after.png", "3-order-and-invoice.png",
         "Saved Order PO000007 and its linked, paid Invoice INV000004",
         [((396, 160, 1460, 250), "Header: proposed No. kept, Date Jul 14 2026 (from the image), Net, "
                                  "Cust.Ref. WEB-2026-0714-A17"),
          ((396, 340, 1380, 410), "Lines: 2 x 250.00 -10% = 450.00 and 3 x 40.00 = 120.00, read back twice"),
          ((1470, 480, 1920, 610), "Totals 570.00 / 108.30 / 678.30 equal the image; discount 0%, free shipping"),
          ((1456, 250, 1710, 320), "Follow-up Invoice (keeps the Order link), not the toolbar's Invoice"),
          ((576, 728, 1920, 756), "Documents > Invoices: INV000004, paid, 678.30; the Order stays open")],
         crop=(0, 0, 1936, 1040)),
]


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate(run_dir: Path, shot: Shot) -> Path:
    image = Image.open(run_dir / shot.source).convert("RGB")
    for box in shot.blur:
        image.paste(image.crop(box).filter(ImageFilter.GaussianBlur(8)), box[:2])
    draw = ImageDraw.Draw(image)
    badge = _font(22)
    for n, (box, _note) in enumerate(shot.boxes, 1):
        draw.rectangle(box, outline=RED, width=4)
        x, y = box[0] - 14, box[1] - 14
        draw.ellipse((x, y, x + 30, y + 30), fill=RED)
        draw.text((x + 15, y + 15), str(n), fill="white", font=badge, anchor="mm")
    if shot.crop:
        image = image.crop(shot.crop)
    text_font, title_font = _font(20), _font(26)
    lines = [(shot.title, title_font)]
    for n, (_box, note) in enumerate(shot.boxes, 1):
        for i, part in enumerate(textwrap.wrap(note, 150)):
            lines.append((f"{n}. {part}" if i == 0 else f"    {part}", text_font))
    height = 24 + sum(34 if f is title_font else 28 for _t, f in lines)
    panel = Image.new("RGB", (image.width, height), (250, 250, 250))
    pd = ImageDraw.Draw(panel)
    y = 12
    for text, font in lines:
        pd.text((20, y), text, fill=RED if font is title_font else (30, 30, 30), font=font)
        y += 34 if font is title_font else 28
    out = Image.new("RGB", (image.width, image.height + height), "white")
    out.paste(panel, (0, 0))
    out.paste(image, (0, height))
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / shot.out
    out.save(target, optimize=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--masters", type=Path, required=True, help="run folder that created the master data")
    parser.add_argument("--order", type=Path, required=True, help="run folder that saved the Order and Invoice")
    args = parser.parse_args()
    for shot in MASTERS:
        print(annotate(args.masters, shot))
    for shot in ORDER:
        print(annotate(args.order, shot))


if __name__ == "__main__":
    main()
