"""Read a Data view's NatTable by Select All + Copy (decision 1, PHASE3_PLAN part C).

NatTable exposes no rows to UIA. The reader:
1. shows the view (its tab, else Data > <view>) and, for Documents, selects the
   category filter in the left tree and verifies it is selected;
2. captures the grid until two captures agree (the list stopped changing);
3. judges emptiness from pixels (Copy on an empty grid crashes Fakturama) and
   checks the header by OCR (the copy has no header row);
4. focuses the grid via UIA, verifies focus, sends Ctrl+A, checks from the
   pixels that exactly the used rows are highlighted (Copy without a selection
   crashes Fakturama), sends Ctrl+C and accepts the clipboard only if its
   sequence number changed and Fakturama owns it;
5. copies twice and requires identical text, parses it as TSV with exactly the
   expected number of columns, and cross-checks the row count with the pixels.

Fakturama copies model values, not display text: e.g. a price of 11.90 arrives
as ``11.899999999999999``, an empty cell as ``null`` (read here as None), dates
as Java ``Tue Nov 03 00:00:00 EET 2026``, states as ``COMMAND_ORDER_PENDING``.
Anything unexpected raises TableError: unreadable is never "zero rows".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageChops

from fakturama_agent.uia.clipboard import ClipboardText
from fakturama_agent.uia.grid import GridPixels, GridUnreadable, analyze, check_header, read_header, selected_bands
from fakturama_agent.uia.locators import TableSpec

ICON_COLUMN = "icon"


class TableError(RuntimeError):
    pass


@dataclass(frozen=True)
class TableRead:
    key: str
    view: str
    filter: str | None
    columns: list[str]
    rows: list[dict[str, str | None]]
    pixels: GridPixels | None = None
    raw: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        return {"table": self.key, "view": self.view, "filter": self.filter, "columns": self.columns, "rows": self.rows}


def column_keys(spec: TableSpec) -> list[str]:
    return [c if c else ICON_COLUMN for c in spec.columns]


def parse_tsv(text: str, spec: TableSpec) -> list[dict[str, str | None]]:
    """Rows separated by CRLF, cells by TAB, no header; every row has exactly the expected columns."""
    if text == "":
        raise TableError("the copy is empty although the grid shows rows")
    lines = text.split("\r\n")
    if lines[-1] == "":
        lines.pop()
    keys = column_keys(spec)
    rows = []
    for i, line in enumerate(lines, 1):
        cells = line.split("\t")
        if len(cells) != len(keys):
            raise TableError(
                f"row {i} has {len(cells)} cells, expected {len(keys)} (a value with a tab or line break, or other columns)"
            )
        if "\n" in line or "\r" in line:
            raise TableError(f"row {i} contains a line break")
        rows.append({k: (None if v == "null" else v) for k, v in zip(keys, cells, strict=True)})
    return rows


# --------------------------------------------------------------------------
# reading, against any backend (live or fake)
# --------------------------------------------------------------------------


class GridBackend(Protocol):
    def show(self, spec: TableSpec) -> None: ...
    def capture(self) -> Image.Image: ...
    def select_all(self) -> None: ...  # focus the grid (verified) and press Ctrl+A
    def copy(self) -> ClipboardText: ...  # Ctrl+C, wait for a new clipboard sequence number


COVERED = "something covers the grid"
OVERLAY_WAIT = 10.0  # seconds a transient overlay (e.g. a notification) may stay before we give up


def _save_overlay_evidence() -> None:
    """Keep a full-screen picture of what covered the grid (out/overlays/), to identify it later."""
    try:
        from datetime import datetime

        from PIL import ImageGrab

        folder = Path("out/overlays")
        folder.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(all_screens=True).save(folder / f"{datetime.now():%Y%m%dT%H%M%S}.png")
    except Exception:  # noqa: BLE001 - evidence must never break a read
        pass


def uncovered_capture(backend, check: Callable[[Image.Image], object], *, stable_timeout: float = 5.0,
                      interval: float = 0.3, wait: float = OVERLAY_WAIT) -> Image.Image:
    """A stable capture that ``check`` accepts. Seen repeatedly (2026-11-06): something drew over
    the bottom rows of a grid for a few seconds (refused as 'used rows after an empty row').
    Reading is read-only, so we look again until it is gone; anything else fails at once."""
    deadline = time.monotonic() + wait
    saved = False
    while True:
        image = stable_capture(backend, stable_timeout, interval)
        try:
            check(image)
            return image
        except (GridUnreadable, TableError) as e:
            if COVERED not in str(e) or time.monotonic() >= deadline:
                raise
            if not saved:
                _save_overlay_evidence()
                saved = True
            time.sleep(1.0)


def stable_capture(backend, timeout: float = 5.0, interval: float = 0.3) -> Image.Image:
    """Capture until two consecutive captures are identical."""
    deadline = time.monotonic() + timeout
    previous = backend.capture()
    while True:
        time.sleep(interval)
        current = backend.capture()
        if current.size == previous.size and ImageChops.difference(current.convert("RGB"), previous.convert("RGB")).getbbox() is None:
            return current
        if time.monotonic() >= deadline:
            raise TableError("the grid kept changing; not reading a list in motion")
        previous = current


def require_selection(image: Image.Image, rows: list[int]) -> None:
    """Copy is only safe with a visible selection: with none, Fakturama 2.2.0 raises
    "copiedCells is null" (an Internal Error dialog). The highlighted bands must be
    exactly ``rows``."""
    try:
        selected = selected_bands(image, analyze(image))
    except GridUnreadable as e:
        raise TableError(f"cannot see the selection: {e}") from e
    if selected != rows:
        raise TableError(f"highlighted rows {selected}, expected {rows}; not copying")


def accept_copy(clip: ClipboardText, fakturama_pid: int, what: str = "copy") -> str:
    if clip.owner_pid != fakturama_pid:
        raise TableError(f"{what}: clipboard owned by pid {clip.owner_pid}, not Fakturama ({fakturama_pid})")
    if clip.text is None:
        raise TableError(f"{what}: the clipboard holds no text")
    return clip.text


def read_table(
    key: str,
    spec: TableSpec,
    backend: GridBackend,
    *,
    fakturama_pid: int,
    ocr: Callable[[bytes], object],
    stable_timeout: float = 5.0,
    interval: float = 0.3,
) -> TableRead:
    backend.show(spec)
    try:
        image = uncovered_capture(backend, analyze, stable_timeout=stable_timeout, interval=interval)
        pixels = analyze(image)
        check_header(read_header(image, pixels, ocr), spec.expected_header(), prefix=spec.checked is not None)
    except GridUnreadable as e:
        raise TableError(f"{spec.view}: {e}") from e
    result = TableRead(key, spec.view, spec.filter, column_keys(spec), [], pixels)
    if pixels.empty:
        return result  # never Copy an empty grid

    texts = []
    for attempt in (1, 2):
        backend.select_all()
        expected = list(range(pixels.ink_rows))
        uncovered_capture(backend, lambda im: require_selection(im, expected), stable_timeout=stable_timeout,
                          interval=interval)
        texts.append(accept_copy(backend.copy(), fakturama_pid, f"copy {attempt}"))
    if texts[0] != texts[1]:
        raise TableError("two copies of the grid differ; the list changed while reading")
    rows = parse_tsv(texts[0], spec)

    if pixels.full and len(rows) < pixels.ink_rows:
        raise TableError(f"copied {len(rows)} rows but the grid shows at least {pixels.ink_rows}")
    if not pixels.full and len(rows) != pixels.ink_rows:
        raise TableError(f"copied {len(rows)} rows but the grid shows {pixels.ink_rows}")
    return TableRead(key, spec.view, spec.filter, column_keys(spec), rows, pixels, texts[0])
