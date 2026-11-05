"""Human-facing views of an OCR run, for the `ocr` command.

- a numbered list of every text box in reading order,
- an optional cross-check table against a RawExtraction,
- an optional overlay image with the numbered boxes drawn on it.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fakturama_agent.extract.crosscheck import CrossCheckResult
from fakturama_agent.extract.ocr import OcrBox, OcrResult
from fakturama_agent.spec.models import CheckStatus

ROW_TOLERANCE_PX = 12  # boxes whose tops differ by less than this count as one line

_STATUS = {CheckStatus.CONFIRMED: "ok", CheckStatus.CONFLICT: "CONFLICT", CheckStatus.UNCONFIRMED: "NOT FOUND"}
_COLOR = {"ok": (0, 150, 60), "low": (220, 120, 0)}
LOW_SCORE = 0.9


def reading_order(boxes: tuple[OcrBox, ...]) -> list[OcrBox]:
    """Top to bottom, then left to right within a visual line."""
    ordered = sorted(boxes, key=lambda b: (b.box[1], b.box[0]))
    lines: list[list[OcrBox]] = []
    for box in ordered:
        if lines and abs(box.box[1] - lines[-1][0].box[1]) < ROW_TOLERANCE_PX:
            lines[-1].append(box)
        else:
            lines.append([box])
    return [b for line in lines for b in sorted(line, key=lambda b: b.box[0])]


def render_boxes(result: OcrResult) -> str:
    boxes = reading_order(result.boxes)
    low = sum(b.score < LOW_SCORE for b in boxes)
    lines = [
        f"{len(boxes)} text boxes, {low} with confidence below {LOW_SCORE} (marked *)",
        "",
        f"{'#':>3}  {'x':>5} {'y':>5}  {'conf':>5}   text",
    ]
    for n, b in enumerate(boxes, 1):
        mark = "*" if b.score < LOW_SCORE else " "
        lines.append(f"{n:>3}  {b.box[0]:>5} {b.box[1]:>5}  {b.score:>5.3f} {mark} {b.text}")
    return "\n".join(lines)


def render_check(checked: CrossCheckResult) -> str:
    width = max((len(c.field) for c in checked.checks), default=10)
    lines = [f"{'field':<{width}}  {'status':<9}  value  [OCR text if different]"]
    for c in checked.checks:
        status = _STATUS[c.status]
        extra = f"  [{c.ocr_text}]" if c.ocr_text else ""
        free = "" if c.critical else "  (free text: warning only)"
        lines.append(f"{c.field:<{width}}  {status:<9}  {c.value}{extra}{free}")
    confirmed = sum(c.status is CheckStatus.CONFIRMED for c in checked.checks)
    lines += [
        "",
        f"{confirmed}/{len(checked.checks)} values confirmed; "
        f"{len(checked.issues)} issue(s) (would stop extract), {len(checked.warnings)} warning(s)",
    ]
    lines += [f"  issue:   {i}" for i in checked.issues]
    lines += [f"  warning: {w}" for w in checked.warnings]
    return "\n".join(lines)


def save_overlay(image: bytes, result: OcrResult, path: Path) -> None:
    """Draw each box with its list number: green, orange when confidence is low."""
    canvas = Image.open(io.BytesIO(image)).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    for n, b in enumerate(reading_order(result.boxes), 1):
        color = _COLOR["low"] if b.score < LOW_SCORE else _COLOR["ok"]
        x0, y0, x1, y1 = b.box
        draw.rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), outline=color, width=2)
        label = str(n)
        tw = draw.textlength(label, font=font)
        # Left of the box so the label never hides a character; above it at the left edge.
        lx, ly = (x0 - tw - 8, y0) if x0 - tw - 8 >= 0 else (x0, max(0, y0 - 19))
        draw.rectangle((lx, ly, lx + tw + 4, ly + 17), fill=color)
        draw.text((lx + 2, ly), label, fill="white", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, format="PNG")
