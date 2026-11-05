"""What a NatTable grid shows, judged from its pixels (no UIA rows exist).

Used for two things before any Copy:
- **emptiness**: Copy on an empty grid crashes Fakturama 2.2.0 with a
  NullPointerException (reference repo), so an empty grid must be recognised
  from its pixels and never copied;
- **header check**: the clipboard copy has no header row, so the header strip
  is read by OCR and must match the expected columns, left to right.

Two recorded styles (2026-11-05, 100% scaling), both with 20-px rows:
- *view* (Data views, Select a product, the Order's items): grid lines of
  RGB(192,192,192) across the whole pane, empty rows drawn as white bands;
- *dialog* (Select the address): a bordered header ending in dark-grey lines,
  a numbered row-header column, alternating white/light-grey rows, lines only
  as wide as the table and plain white below the last row.
A grid line is a row whose dominant colour is a mid grey covering at least
90% of the table width (the widest non-white extent of the header band).
A row band with no "ink" (text, icons, selection) ends the list. A selected row
is a band whose header-column extent is mostly the selection blue: (0,120,212) in view style,
(217,232,251) in dialog style. Anything that does not fit is unreadable, never
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
LINE_COVER = 0.9  # a grid-line row: one mid-grey colour over 90% of the table width
SELECT_COVER = 0.5  # a selected row: selection blue over half the table width
MIN_INK_PIXELS = 8  # anti-aliased text of one short value is far above this
MIN_PITCH, MAX_PITCH = 12, 80
WHITE = (255, 255, 255)


class GridUnreadable(RuntimeError):
    pass


@dataclass(frozen=True)
class GridPixels:
    header_bottom: int  # y of the (last) line under the header
    pitch: int  # row height including its grid line
    bands: int  # complete row bands visible
    ink_rows: int  # leading bands with ink
    table_width: int = 0  # widest non-white extent near the top (grid lines may span the whole pane)
    columns_width: int = 0  # extent of the header cells: how far rows and selections are drawn

    @property
    def empty(self) -> bool:
        return self.ink_rows == 0

    @property
    def full(self) -> bool:
        """Every visible band is used: more rows may be scrolled out of view."""
        return self.ink_rows == self.bands

    def band(self, i: int) -> tuple[int, int]:
        """Top and bottom y (inclusive, grid-relative) of row band ``i``, without its grid line."""
        top = self.header_bottom + 1 + i * self.pitch
        return top, top + self.pitch - 2

    def click_point(self, i: int) -> tuple[int, int]:
        """A point inside row ``i``, in its first data column, grid-relative."""
        top, bottom = self.band(i)
        return min(80, max(10, self.columns_width // 8)), (top + bottom) // 2


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


def _is_line_grey(p: tuple[int, int, int]) -> bool:
    r, g, b = p
    return r == g == b and 90 <= r <= 200


def _is_selection(p: tuple[int, int, int]) -> bool:
    r, g, b = p
    return b >= 200 and b - r >= 25 and p != WHITE


def analyze(image: Image.Image) -> GridPixels:
    img = image.convert("RGB")
    width, height = img.size
    if width < 50 or height < 3 * MIN_PITCH:
        raise GridUnreadable(f"grid image too small ({width}x{height})")
    px = img.load()
    rows = [Counter(px[x, y] for x in range(width)) for y in range(height)]
    top_rows = rows[: min(height, MAX_PITCH)]
    table_width = max(width - r[WHITE] for r in top_rows)
    if table_width < 50:
        raise GridUnreadable("no table header found (the top of the grid is blank)")

    def is_line(r: Counter) -> bool:
        coloured = [(c, n) for c, n in r.most_common(3) if c != WHITE]
        return bool(coloured) and _is_line_grey(coloured[0][0]) and coloured[0][1] >= LINE_COVER * table_width

    runs = _runs([y for y, r in enumerate(rows) if is_line(r)])
    # The header's bottom line: the first grey run at least MIN_PITCH down whose next
    # line (if any) is one row height away. A grey header band broken by its own
    # text (Documents, a dialog with a selected row) yields shorter runs above it.
    header_run = None
    for i, (start, end) in enumerate(runs):
        if not MIN_PITCH - 1 <= end <= MAX_PITCH:
            continue
        nxt = runs[i + 1][0] if i + 1 < len(runs) else None
        if nxt is None or MIN_PITCH <= nxt - end <= MAX_PITCH:
            header_run = i
            break
    if header_run is None:
        raise GridUnreadable("no header line near the top; not a NatTable grid as recorded")
    header_bottom = runs[header_run][1]
    line_ys = {y for start, end in runs for y in range(start, end + 1)}
    columns_width = max((width - rows[y][WHITE] for y in range(header_bottom) if y not in line_ys), default=0)
    if columns_width < 50:
        raise GridUnreadable("the header band has no columns")
    starts = [r[0] for r in runs[header_run + 1 :]]
    diffs = Counter(b - a for a, b in zip([header_bottom, *starts], starts, strict=False))
    common = diffs.most_common(1)
    if common and common[0][1] >= 2:
        pitch = common[0][0]
    else:  # fewer than two row lines (e.g. an empty dialog table): rows are as high as the header
        pitch = header_bottom + 1
    if not MIN_PITCH <= pitch <= MAX_PITCH:
        raise GridUnreadable(f"row height not recognised (line spacing {dict(diffs)})")
    misaligned = [y for y in starts if (y - header_bottom) % pitch]
    if misaligned:
        raise GridUnreadable(f"grid lines at y={misaligned[:3]} do not follow the {pitch}-px rows")

    bands = (height - 1 - header_bottom) // pitch
    inked = []
    for i in range(bands):
        top = header_bottom + 1 + i * pitch
        ink = sum(sum(n for colour, n in rows[y].items() if _is_ink(colour)) for y in range(top, top + pitch - 1))
        inked.append(ink >= MIN_INK_PIXELS)
    ink_rows = inked.index(False) if False in inked else len(inked)
    if any(inked[ink_rows:]):
        raise GridUnreadable(f"used rows after an empty row (bands {inked}); something covers the grid")
    return GridPixels(header_bottom, pitch, bands, ink_rows, table_width, columns_width)


def selected_bands(image: Image.Image, pixels: GridPixels) -> list[int]:
    """Row bands drawn with the selection colour (Copy is only safe when this is not empty)."""
    img = image.convert("RGB")
    px = img.load()
    selected = []
    for i in range(pixels.bands):
        top, bottom = pixels.band(i)
        y = (top + bottom) // 2
        blue = sum(_is_selection(px[x, y]) for x in range(img.width))
        if blue >= SELECT_COVER * pixels.columns_width:
            selected.append(i)
    return selected


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


def check_header(texts_left_to_right: list[str], expected: list[str], *, prefix: bool = False) -> None:
    """OCR'd header texts must equal the expected non-empty headers, in order (spaces ignored).

    With ``prefix``, only the leading expected headers are compared (later ones may
    be truncated by narrow columns)."""
    seen = [_norm(t) for t in texts_left_to_right if _norm(t)]
    want = [_norm(c) for c in expected if _norm(c)]
    if prefix:
        seen = seen[: len(want)]
    if seen != want:
        raise GridUnreadable(f"grid header reads {seen}, expected {want} (columns moved, hidden or renamed?)")


def read_header(image: Image.Image, pixels: GridPixels, ocr: Callable[[bytes], object]) -> list[str]:
    result = ocr(header_strip(image, pixels))
    boxes = sorted(result.boxes, key=lambda b: b.box[0])
    return [b.text for b in boxes]
