"""How Fakturama displays money, learned from its own Preferences (never changed).

Preferences > General shows an ``Example`` field: the number -1234.57 (rounded
to the configured decimal places) formatted with the current currency settings,
e.g. ``$-1,234.57`` (United States) or ``-1.234,57 €`` (Germany). Preflight reads
it and builds a ``MoneyFormat``; every amount read back from Fakturama is parsed
with that format. A format we cannot pin down unambiguously stops the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_SPACES = re.compile(r"[\s  ]+")
# The example is -1234.57: optional text, a sign, "1", maybe a group mark, "234",
# then a decimal mark and the fraction digits, optional text.
_EXAMPLE = re.compile(
    r"^(?P<prefix>[^\d\-]*?)(?P<sign>-?)(?P<prefix2>[^\d\-]*?)"
    r"1(?P<group>[^\d]?)234(?:(?P<decimal>[^\d])(?P<frac>\d+))?"
    r"(?P<suffix>[^\d]*)$"
)


class MoneyFormatError(ValueError):
    pass


@dataclass(frozen=True)
class MoneyFormat:
    symbol: str  # "" when "no symbol" is configured; may be an ISO code such as "EUR"
    symbol_first: bool
    group: str  # "" when the thousands separator is off
    decimal: str
    decimals: int

    @classmethod
    def from_example(cls, example: str, decimals: int) -> MoneyFormat:
        text = _SPACES.sub(" ", example).strip()
        m = _EXAMPLE.match(text)
        if m is None or not m["sign"]:
            raise MoneyFormatError(f"currency example {example!r} is not -1234.57 in a recognisable format")
        prefix = (m["prefix"] + m["prefix2"]).strip()
        suffix = m["suffix"].strip()
        if prefix and suffix:
            raise MoneyFormatError(f"currency example {example!r} has text on both sides of the number")
        if decimals < 1:
            raise MoneyFormatError("currency amounts without decimal places are not supported")
        frac = m["frac"] or ""
        if len(frac) != decimals:
            raise MoneyFormatError(f"currency example {example!r} shows {len(frac)} decimals, preferences say {decimals}")
        if not frac.startswith("5"):
            raise MoneyFormatError(f"currency example {example!r}: fraction {frac!r} is not .57…")
        decimal = m["decimal"] or ""
        if decimals and not decimal:
            raise MoneyFormatError(f"currency example {example!r} has no decimal mark")
        group = m["group"] or ""
        group = " " if group.isspace() else group
        if group and group == decimal:
            raise MoneyFormatError(f"currency example {example!r}: group and decimal mark are both {group!r}")
        return cls(symbol=prefix or suffix, symbol_first=bool(prefix) or not suffix, group=group, decimal=decimal, decimals=decimals)

    def parse(self, text: str) -> Decimal:
        """Parse an amount as Fakturama displays it; the symbol is optional. Strict: anything else fails."""
        t = _SPACES.sub(" ", text).strip()
        if self.symbol:
            if self.symbol_first:
                for head in (self.symbol, "-" + self.symbol):
                    if t.startswith(head):
                        t = ("-" if head.startswith("-") else "") + t[len(head):]
                        break
            elif t.endswith(self.symbol):
                t = t[: -len(self.symbol)]
        t = t.strip()
        neg = t.startswith("-")
        body = t[1:].strip() if neg else t
        g = re.escape(self.group) if self.group else None
        d = re.escape(self.decimal) if self.decimal else None
        int_part = rf"\d{{1,3}}(?:{g}\d{{3}})*|\d+" if g else r"\d+"
        frac_part = rf"(?:{d}\d+)?" if d else ""
        if not re.fullmatch(rf"(?:{int_part}){frac_part}", body):
            raise MoneyFormatError(f"{text!r} is not an amount in the format {self.describe()}")
        if g:
            body = body.replace(self.group, "")
        if d:
            body = body.replace(self.decimal, ".")
        value = Decimal(body)
        return -value if neg else value

    def describe(self) -> str:
        sample = f"1{self.group}234{self.decimal}{'5' * self.decimals}"
        return f"{self.symbol}{sample}" if self.symbol_first else f"{sample} {self.symbol}".rstrip()
