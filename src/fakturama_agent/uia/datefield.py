"""Set a segmented CDateTime (Fakturama's Date field) and verify it.

Observed live (2026-11-05, English UI): the field shows ``MMM d, yyyy`` (e.g.
``Nov 5, 2026``); setting the whole Value does not persist (findings). With the
field focused, one segment (month, day or year) is active, and which one is
remembered from earlier use. Up/Down change only the active segment, Right
moves to the next segment (wrapping year -> month), and digits typed into a
segment fill it and auto-advance once it is full.

So nothing about the active segment is assumed: it is found by **probes**
(Up then Down, and Down then Up; the segment changed by both is active, see
``active_segment``). Segments are entered as zero-padded digits in the order year, month,
day (no invalid in-between dates such as 31 Feb), each after a probe confirms
the right segment is active. The caller then moves focus away and checks the
displayed date; any surprise raises DateEntryError (manual review).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SEGMENTS = ("month", "day", "year")
_DISPLAY = re.compile(r"^(?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{1,4})$")


class DateEntryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Shown:
    month: int
    day: int
    year: int

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.month, self.day, self.year)


def parse_display(text: str) -> Shown:
    m = _DISPLAY.match((text or "").strip())
    if m is None or m["month"] not in MONTHS:
        raise DateEntryError(f"date field shows {text!r}, not 'MMM d, yyyy'")
    return Shown(MONTHS.index(m["month"]) + 1, int(m["day"]), int(m["year"]))


def display(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def shows(text: str, d: date) -> bool:
    try:
        s = parse_display(text)
    except DateEntryError:
        return False
    return s.as_tuple() == (d.month, d.day, d.year)


class DateWidget(Protocol):
    def read(self) -> str: ...
    def key(self, keys: str) -> None: ...


def _changed(a: Shown, b: Shown) -> set[str]:
    return {seg for seg, x, y in zip(SEGMENTS, a.as_tuple(), b.as_tuple(), strict=True) if x != y}


def active_segment(w: DateWidget) -> str:
    """Probe Up (then Down back) and Down (then Up back); the active segment changes both times.

    One probe alone is ambiguous near edges: a month step can clamp the day
    (Mar 31 -> Apr 30) and a day step can cross into the next month (Jul 31 ->
    Aug 1), but such side effects happen in one direction only. The most
    significant segment changed by both probes is the active one, and its value
    must come back after each probe.
    """
    before = parse_display(w.read())
    changes = []
    for there, back in (("{UP}", "{DOWN}"), ("{DOWN}", "{UP}")):
        w.key(there)
        probed = parse_display(w.read())
        w.key(back)
        changes.append(_changed(before, probed))
    both = changes[0] & changes[1]
    if not both:
        raise DateEntryError(f"probes changed {changes[0] or 'nothing'} and {changes[1] or 'nothing'}; no active segment")
    segment = next(s for s in ("year", "month", "day") if s in both)
    after = parse_display(w.read())
    if getattr(after, segment) != getattr(before, segment):
        raise DateEntryError(f"the {segment} did not come back after probing ({before} -> {after})")
    return segment


def goto_segment(w: DateWidget, segment: str) -> None:
    for _ in range(len(SEGMENTS) + 1):
        if active_segment(w) == segment:
            return
        w.key("{RIGHT}")
    raise DateEntryError(f"could not reach the {segment} segment")


def enter_date(w: DateWidget, target: date) -> None:
    """Type the target into the focused date field, segment by segment. Caller verifies after blur."""
    digits = {"year": f"{target.year:04d}", "month": f"{target.month:02d}", "day": f"{target.day:02d}"}
    for segment in ("year", "month", "day"):
        goto_segment(w, segment)
        for ch in digits[segment]:
            w.key(ch)
        shown = parse_display(w.read())
        if getattr(shown, segment) != getattr(target, segment):
            raise DateEntryError(f"{segment} shows {getattr(shown, segment)} after typing {digits[segment]}")
