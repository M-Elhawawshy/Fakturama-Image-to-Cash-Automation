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

    def typed(self, value: Decimal) -> str:
        """What to type into a money field (a product's gross price, an Invoice's paid Value):
        the workspace's decimal mark, no grouping, no symbol. Recorded live (2026-11-06,
        Germany): '297,50' is 297,50 EUR, while '297.50' is read as 29.750,00 EUR and
        '12.34' as 1.234,00 EUR. Not for NatTable line cells or the VAT Value, which always
        take a dot ('1,5' -> 15, '7,5' -> 75 %)."""
        text = f"{value.quantize(Decimal(1).scaleb(-self.decimals)):f}"
        return text.replace(".", self.decimal) if self.decimal else text

    def describe(self) -> str:
        sample = f"1{self.group}234{self.decimal}{'5' * self.decimals}"
        return f"{self.symbol}{sample}" if self.symbol_first else f"{sample} {self.symbol}".rstrip()


# --------------------------------------------------------------------------
# which currency the workspace is in
# --------------------------------------------------------------------------

# Fakturama 2.2.0 has ONE currency for the whole workspace (Preferences > General >
# Currency locale, a country name); documents have no currency of their own. The
# run only reads it, to compare it with the order's currency; changing it is the
# user's job, never the automation's.
_EURO = ("Austria", "Belgium", "Croatia", "Cyprus", "Estonia", "Finland", "France", "Germany", "Greece",
         "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Portugal",
         "Slovakia", "Slovenia", "Spain")
COUNTRY_CURRENCY: dict[str, str] = {
    **{country: "EUR" for country in _EURO},
    "United States": "USD", "United Kingdom": "GBP", "Switzerland": "CHF", "Liechtenstein": "CHF",
    "Canada": "CAD", "Australia": "AUD", "New Zealand": "NZD", "Japan": "JPY", "China": "CNY",
    "Sweden": "SEK", "Norway": "NOK", "Denmark": "DKK", "Poland": "PLN", "Czech Republic": "CZK",
    "Czechia": "CZK", "Hungary": "HUF",
}
SYMBOL_CURRENCIES: dict[str, set[str]] = {
    "$": {"USD", "CAD", "AUD", "NZD"}, "€": {"EUR"}, "£": {"GBP"}, "¥": {"JPY", "CNY"},
    "kr": {"SEK", "NOK", "DKK"}, "zł": {"PLN"}, "Kč": {"CZK"}, "Ft": {"HUF"},
}
_ISO = re.compile(r"^[A-Z]{3}$")


def workspace_currency(locale: str | None, money: MoneyFormat | None) -> tuple[str | None, str]:
    """Pure: (ISO code, how it was found) of the workspace currency, or (None, why not).

    From the Currency locale (a country) and the symbol in Preferences' example; both
    must agree. Unknown or contradicting settings give None (the run stops for review).
    """
    if money is None:
        return None, "the currency format was not read"
    by_locale = COUNTRY_CURRENCY.get((locale or "").strip())
    symbol = money.symbol.strip()
    seen = f"locale {locale!r}, symbol {symbol!r}"
    if _ISO.match(symbol):
        if by_locale and by_locale != symbol:
            return None, f"{seen} disagree ({by_locale} vs {symbol})"
        return symbol, seen
    if by_locale is None:
        return None, f"{seen}: the locale's currency is not known to the tool"
    if symbol and by_locale not in SYMBOL_CURRENCIES.get(symbol, set()):
        return None, f"{seen}: the symbol does not fit {by_locale}"
    return by_locale, seen
