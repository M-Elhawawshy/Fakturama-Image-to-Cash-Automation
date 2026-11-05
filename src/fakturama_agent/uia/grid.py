"""What a NatTable grid shows, judged from its pixels (no UIA rows exist).

Used for two things before any Copy:
- **emptiness**: Copy on an empty grid crashes Fakturama 2.2.0 with a
  NullPointerException (reference repo), so an empty grid must be recognised
  from its pixels and never copied;
- **header check**: the clipboard copy has no header row, so the header strip
  is read by OCR and must match the expected columns, left to right.

Recorded layout (2026-11-05, 100% scaling): a header band, then rows of equal
height separated by 1-px grid lines of RGB (192, 192, 192). An empty row holds
only white and grid-line pixels; a used row has text, icons or the selection
colour ("ink"). Anything that does not fit this picture is unreadable, never
"empty".
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image

GRID_LINE = (192, 192, 192)
LINE_FRACTION = 0.5  # a grid-line row is at least half grid-line pixels
MIN_INK_PIXELS = 8  # anti-aliased text of one short value is far above this
MIN_PITCH, MAX_PITCH = 12, 80


class GridUnreadable(RuntimeError):
    pass


@dataclass(frozen=True)
class GridPixels:
    header_bottom: int  # y of the line under the header
    pitch: int  # row height including its grid line
    bands: int  # complete row bands visible
    ink_rows: int  # leading bands with ink

    @property
    def empty(self) -> bool:
        return self.ink_rows == 0

    @property
    def full(self) -> bool:
        """Every visible band is used: more rows may be scrolled out of view."""
        return self.ink_rows == self.bands


def _runs(ys: Sequence[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for y in ys:
        if runs and y == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], y)
        else:
            runs.append((y, y))
    return runs


def _is_ink(p: tuple[int, int, int]) -> bool:
    return p != GRID_LINE and min(p) < 235


def analyze(image: Image.Image) -> GridPixels:
    img = image.convert("RGB")
    width, height = img.size
    if width < 50 or height < 3 * MIN_PITCH:
        raise GridUnreadable(f"grid image too small ({width}x{height})")
    px = img.load()
    line_rows = [y for y in range(height) if sum(px[x, y] == GRID_LINE for x in range(width)) >= LINE_FRACTION * width]
    runs = _runs(line_rows)
    if len(runs) < 3:
        raise GridUnreadable(f"found {len(runs)} grid-line runs; not a NatTable grid as recorded")
    header_bottom = runs[0][1]
    if header_bottom > MAX_PITCH:
        raise GridUnreadable(f"no header line near the top (first line at y={header_bottom})")
    starts = [r[0] for r in runs[1:]]
    diffs = Counter(b - a for a, b in zip([header_bottom, *starts], starts, strict=False))
    pitch, seen = diffs.most_common(1)[0]
    if not MIN_PITCH <= pitch <= MAX_PITCH or seen < 2:
        raise GridUnreadable(f"row height not recognised (line spacing {dict(diffs)})")

    bands = (height - 1 - header_bottom) // pitch
    inked = []
    for i in range(bands):
        top, bottom = header_bottom + 1 + i * pitch, header_bottom + (i + 1) * pitch  # bottom = next line
        ink = sum(_is_ink(px[x, y]) for y in range(top, bottom) for x in range(width))
        inked.append(ink >= MIN_INK_PIXELS)
    ink_rows = inked.index(False) if False in inked else len(inked)
    if any(inked[ink_rows:]):
        raise GridUnreadable(f"used rows after an empty row (bands {inked}); something covers the grid")
    return GridPixels(header_bottom=header_bottom, pitch=pitch, bands=bands, ink_rows=ink_rows)


def header_strip(image: Image.Image, pixels: GridPixels, scale: int = 3) -> bytes:
    """The header band, upscaled for OCR (9-pt UI text reads reliably at 3x)."""
    img = image.convert("RGB")
    strip = img.crop((0, 0, img.width, pixels.header_bottom))
    strip = strip.resize((strip.width * scale, strip.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    strip.save(buf, "PNG")
    return buf.getvalue()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def check_header(texts_left_to_right: list[str], expected: list[str]) -> None:
    """OCR'd header texts must equal the expected non-empty headers, in order (spaces ignored)."""
    seen = [_norm(t) for t in texts_left_to_right if _norm(t)]
    want = [_norm(c) for c in expected if _norm(c)]
    if seen != want:
        raise GridUnreadable(f"grid header reads {seen}, expected {want} (columns moved, hidden or renamed?)")


def read_header(image: Image.Image, pixels: GridPixels, ocr: Callable[[bytes], object]) -> list[str]:
    result = ocr(header_strip(image, pixels))
    boxes = sorted(result.boxes, key=lambda b: b.box[0])
    return [b.text for b in boxes]
