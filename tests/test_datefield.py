"""Date entry against a simulated CDateTime that behaves as observed live (2026-11-05)."""

import calendar
from datetime import date

import pytest

from fakturama_agent.uia.datefield import (
    DateEntryError,
    active_segment,
    display,
    enter_date,
    parse_display,
    shows,
)

SEGS = ("month", "day", "year")
WIDTH = {"month": 2, "day": 2, "year": 4}


def _clamp(y, m, d):
    return min(d, calendar.monthrange(y, m)[1])


class FakeDateTime:
    """Calendar.add semantics: month/year steps clamp the day, day steps cross months.
    One segment active (remembered), Right wraps year -> month, digits fill the active
    segment and auto-advance when it is full."""

    def __init__(self, start: date, active: str = "month", *, ignore_digits: bool = False, stuck: bool = False):
        self.d = start
        self.active = SEGS.index(active)
        self.buffer = ""
        self.ignore_digits = ignore_digits
        self.stuck = stuck  # arrows do nothing (focus elsewhere)
        self.keys = []

    def read(self) -> str:
        return display(self.d)

    def _step(self, n: int) -> None:
        y, m, d = self.d.year, self.d.month, self.d.day
        seg = SEGS[self.active]
        if seg == "year":
            self.d = date(y + n, m, _clamp(y + n, m, d))
        elif seg == "month":
            total = y * 12 + (m - 1) + n
            ny, nm = divmod(total, 12)
            self.d = date(ny, nm + 1, _clamp(ny, nm + 1, d))
        else:
            self.d = date.fromordinal(self.d.toordinal() + n)

    def key(self, k: str) -> None:
        self.keys.append(k)
        if self.stuck:
            return
        if k in ("{UP}", "{DOWN}"):
            self.buffer = ""
            self._step(1 if k == "{UP}" else -1)
        elif k == "{RIGHT}":
            self.buffer = ""
            self.active = (self.active + 1) % 3
        elif k.isdigit() and not self.ignore_digits:
            seg = SEGS[self.active]
            self.buffer += k
            if len(self.buffer) == WIDTH[seg]:
                v = int(self.buffer)
                y, m, d = self.d.year, self.d.month, self.d.day
                if seg == "year":
                    self.d = date(v, m, _clamp(v, m, d))
                elif seg == "month" and 1 <= v <= 12:
                    self.d = date(y, v, _clamp(y, v, d))
                elif seg == "day" and 1 <= v <= calendar.monthrange(y, m)[1]:
                    self.d = date(y, m, v)
                self.buffer = ""
                self.active = (self.active + 1) % 3


def test_display_format_seen_live():
    assert display(date(2026, 11, 5)) == "Nov 5, 2026"
    assert parse_display("Jul 14, 2026").as_tuple() == (7, 14, 2026)
    assert parse_display("Nov 03, 2026").as_tuple() == (11, 3, 2026)  # zero-padded variant (findings)
    assert parse_display("Nov 20, 26").year == 26  # a half-typed year seen live
    assert shows("Jul 14, 2026", date(2026, 7, 14)) and not shows("Jul 14, 2025", date(2026, 7, 14))
    for bad in ("2026-07-14", "14.07.2026", "Jux 14, 2026", "", "01 5, 7"):
        with pytest.raises(DateEntryError):
            parse_display(bad)


EDGE_STARTS = [date(2026, 11, 5), date(2026, 1, 31), date(2026, 3, 31), date(2026, 7, 31), date(2028, 2, 29),
               date(2025, 12, 31), date(2026, 1, 1), date(2026, 2, 28)]


@pytest.mark.parametrize("start", EDGE_STARTS)
@pytest.mark.parametrize("seg", SEGS)
def test_probe_finds_the_active_segment_at_edges(start, seg):
    w = FakeDateTime(start, seg)
    assert active_segment(w) == seg


@pytest.mark.parametrize("start", EDGE_STARTS)
@pytest.mark.parametrize("seg", SEGS)
@pytest.mark.parametrize("target", [date(2026, 7, 14), date(2028, 2, 29), date(2025, 12, 31), date(2027, 1, 1),
                                    date(2026, 2, 28), date(2026, 4, 30)])
def test_enter_date_from_any_state(start, seg, target):
    w = FakeDateTime(start, seg)
    enter_date(w, target)
    assert w.d == target


def test_sample_order_date_like_the_live_run():
    w = FakeDateTime(date(2026, 11, 20), "month")  # state seen before the live run
    enter_date(w, date(2026, 7, 14))
    assert w.read() == "Jul 14, 2026"


def test_arrows_without_effect_are_refused():
    with pytest.raises(DateEntryError, match="no active segment"):
        active_segment(FakeDateTime(date(2026, 11, 5), stuck=True))


def test_ignored_digits_are_detected():
    with pytest.raises(DateEntryError, match="year shows"):
        enter_date(FakeDateTime(date(2026, 11, 5), "year", ignore_digits=True), date(2027, 7, 14))


def test_unparseable_display_stops():
    class Odd(FakeDateTime):
        def read(self):
            return "14.07.2026"

    with pytest.raises(DateEntryError, match="MMM d, yyyy"):
        enter_date(Odd(date(2026, 7, 14)), date(2026, 7, 14))
