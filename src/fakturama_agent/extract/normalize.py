"""RawExtraction (verbatim strings) -> OrderSpec (typed + derived).

Parsing is deliberately strict: a value that does not match an unambiguous
format becomes an Issue (manual review) instead of a best guess. All issues are
collected so one review report can list every problem.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TypeVar

from fakturama_agent.spec.derive import canonical_number, compute_derived
from fakturama_agent.spec.models import (
    Address,
    Debtor,
    Issue,
    Item,
    Order,
    OrderSpec,
    PaidStatus,
    Payment,
    PaymentMethod,
    Provenance,
    RawExtraction,
    RawItem,
    Totals,
)

T = TypeVar("T")


class ParseError(ValueError):
    """Raised by a parser; ``expected`` describes the accepted format."""

    def __init__(self, expected: str) -> None:
        super().__init__(expected)
        self.expected = expected


# --------------------------------------------------------------------------
# Field parsers (pure; raise ParseError)
# --------------------------------------------------------------------------

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DOT_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")  # DD.MM.YYYY, unambiguous


def parse_date(text: str) -> dt.date:
    s = text.strip()
    if m := _ISO_DATE.match(s):
        y, mo, d = m.groups()
    elif m := _DOT_DATE.match(s):
        d, mo, y = m.groups()
    else:
        # Slash forms (01/02/2026) are day/month ambiguous: never guess.
        raise ParseError("date as YYYY-MM-DD or DD.MM.YYYY")
    try:
        return dt.date(int(y), int(mo), int(d))
    except ValueError:
        raise ParseError("a valid calendar date") from None


# Plain "1234.56" or comma-grouped "1,234.56". A comma without a dot (e.g.
# "1,234" or "250,00") is locale-ambiguous, so it is rejected.
_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")
_GROUPED_NUMBER = re.compile(r"^[+-]?\d{1,3}(,\d{3})+\.\d+$")


def parse_decimal(text: str) -> Decimal:
    s = text.strip()
    if not (_PLAIN_NUMBER.match(s) or _GROUPED_NUMBER.match(s)):
        raise ParseError("number like 1234.56 or 1,234.56")
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:  # pragma: no cover - regex already guarantees validity
        raise ParseError("number like 1234.56") from None


_MONEY = re.compile(r"^(?P<pre>[A-Z]{3}|€)?\s*(?P<num>[^\sA-Z€]+)\s*(?P<post>[A-Z]{3}|€)?$")


def parse_money(text: str, currency: str | None) -> Decimal:
    """``EUR 570.00`` / ``570.00 EUR`` / ``€570.00`` / ``570.00`` -> Decimal('570.00').

    A currency token must agree with the order currency.
    """
    m = _MONEY.match(text.strip())
    if not m or (m["pre"] and m["post"]):
        raise ParseError("amount with optional currency code, e.g. EUR 570.00")
    token = m["pre"] or m["post"]
    if token:
        symbol_ok = token == "€" and currency == "EUR"
        if token != currency and not symbol_ok:
            raise ParseError(f"amount in order currency {currency}")
    return parse_decimal(m["num"])


_PERCENT = re.compile(r"^(?P<num>[^%\s]+)\s*%?$")


def parse_percent(text: str) -> Decimal:
    """``10%`` / ``10 %`` / ``10`` / ``19.0%`` -> Decimal('10') / Decimal('19')."""
    m = _PERCENT.match(text.strip())
    if not m:
        raise ParseError("percentage like 19%")
    value = parse_decimal(m["num"])
    if not Decimal(0) <= value <= Decimal(100):
        raise ParseError("percentage between 0 and 100")
    return canonical_number(value)


def parse_quantity(text: str) -> Decimal:
    value = parse_decimal(text)
    if value <= 0:
        raise ParseError("positive quantity")
    return canonical_number(value)


def parse_int(text: str) -> int:
    s = text.strip()
    if not s.isdigit():
        raise ParseError("positive integer")
    return int(s)


def split_contact_name(text: str) -> tuple[str, str]:
    """Exactly two tokens -> (first, last). Anything else needs a human."""
    tokens = text.split()
    if len(tokens) != 2:
        raise ParseError("contact name of exactly two words (first last)")
    return tokens[0], tokens[1]


def map_paid_status(text: str) -> PaidStatus:
    """Anything other than PAID (case-insensitive) is UNPAID."""
    return PaidStatus.PAID if text.strip().upper() == "PAID" else PaidStatus.UNPAID


_PAYMENT_METHODS = {m.value.casefold(): m for m in PaymentMethod}


def map_payment_method(text: str) -> PaymentMethod:
    """Closed enum; matches case-insensitively with collapsed whitespace, no synonyms."""
    key = " ".join(text.split()).casefold()
    if key not in _PAYMENT_METHODS:
        raise ParseError(" | ".join(m.value for m in PaymentMethod))
    return _PAYMENT_METHODS[key]


# Canonical English name and ISO 3166-1 alpha-2. Extend as new samples appear;
# an unlisted country goes to manual review.
_COUNTRIES: dict[str, tuple[str, str]] = {
    "germany": ("Germany", "DE"),
    "deutschland": ("Germany", "DE"),
    "de": ("Germany", "DE"),
    "austria": ("Austria", "AT"),
    "österreich": ("Austria", "AT"),
    "at": ("Austria", "AT"),
    "switzerland": ("Switzerland", "CH"),
    "schweiz": ("Switzerland", "CH"),
    "ch": ("Switzerland", "CH"),
    "france": ("France", "FR"),
    "fr": ("France", "FR"),
    "netherlands": ("Netherlands", "NL"),
    "nl": ("Netherlands", "NL"),
    "belgium": ("Belgium", "BE"),
    "be": ("Belgium", "BE"),
    "italy": ("Italy", "IT"),
    "it": ("Italy", "IT"),
    "spain": ("Spain", "ES"),
    "es": ("Spain", "ES"),
    "united kingdom": ("United Kingdom", "GB"),
    "gb": ("United Kingdom", "GB"),
    "united states": ("United States", "US"),
    "us": ("United States", "US"),
}


def map_country(text: str) -> tuple[str, str]:
    key = " ".join(text.split()).casefold()
    if key not in _COUNTRIES:
        raise ParseError("a known country name or ISO code")
    return _COUNTRIES[key]


# --------------------------------------------------------------------------
# Normalizer
# --------------------------------------------------------------------------


@dataclass
class NormalizeResult:
    spec: OrderSpec | None
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.spec is not None and not self.issues


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def text(self, raw: str | None, name: str, *, required: bool = True) -> str | None:
        value = raw.strip() if raw is not None else None
        if not value:
            if required:
                self.issues.append(Issue(rule="required", field=name, expected="non-empty value", observed=raw))
            return None
        return value

    def parse(self, raw: str | None, name: str, parser, *args, required: bool = True):
        value = self.text(raw, name, required=required)
        if value is None:
            return None
        try:
            return parser(value, *args)
        except ParseError as e:
            self.issues.append(Issue(rule=parser.__name__, field=name, expected=e.expected, observed=raw))
            return None


_ADDRESS_FIELDS = ("name", "street", "zip", "city", "country")


def _address(c: _Collector, raw: RawExtraction, prefix: str) -> Address | None:
    values = {f: getattr(raw, f"{prefix}_{f}") for f in _ADDRESS_FIELDS}
    texts = {f: c.text(v, f"{prefix}_address.{f}") for f, v in values.items()}
    country = c.parse(values["country"], f"{prefix}_address.country", map_country) if texts["country"] else None
    if None in texts.values() or country is None:
        return None
    name, code = country
    return Address(
        name=texts["name"], street=texts["street"], zip=texts["zip"], city=texts["city"], country=name, country_code=code
    )


def _item(c: _Collector, raw: RawItem, i: int, currency: str | None) -> Item | None:
    p = f"items[{i}]"
    values = dict(
        line_no=c.parse(raw.line_no, f"{p}.line_no", parse_int),
        sku=c.text(raw.sku, f"{p}.sku"),
        description=c.text(raw.description, f"{p}.description"),
        quantity=c.parse(raw.quantity, f"{p}.quantity", parse_quantity),
        unit=c.text(raw.unit, f"{p}.unit", required=False),
        unit_net_price=c.parse(raw.unit_net_price, f"{p}.unit_net_price", parse_money, currency),
        discount_percent=c.parse(raw.discount, f"{p}.discount", parse_percent, required=False),
        vat_percent=c.parse(raw.vat_rate, f"{p}.vat_rate", parse_percent),
        line_net=c.parse(raw.line_net, f"{p}.line_net", parse_money, currency),
    )
    if raw.discount is None or not raw.discount.strip():
        values["discount_percent"] = Decimal(0)  # no discount column/value printed
    optional = {"unit"}
    if any(v is None for k, v in values.items() if k not in optional):
        return None
    return Item(**values)


def normalize(raw: RawExtraction, provenance: Provenance | None = None) -> NormalizeResult:
    c = _Collector()

    currency = c.text(raw.currency, "order.currency")
    order_vals = dict(
        external_reference=c.text(raw.external_reference, "order.external_reference"),
        order_date=c.parse(raw.order_date, "order.order_date", parse_date),
        customer_id=c.text(raw.customer_id, "order.customer_id", required=False),
        currency=currency,
    )

    names = c.parse(raw.contact_name, "debtor.contact_name", split_contact_name)
    company = c.text(raw.company, "debtor.company")

    billing = _address(c, raw, "billing")
    delivery_printed = any(getattr(raw, f"delivery_{f}") not in (None, "") for f in _ADDRESS_FIELDS)
    delivery = _address(c, raw, "delivery") if delivery_printed else None

    method = c.parse(raw.payment_method, "payment.method", map_payment_method)
    paid_status = c.parse(raw.paid_status, "payment.paid_status", map_paid_status)
    badge = c.parse(raw.status_badge, "order.status_badge", map_paid_status, required=False)
    if paid_status is not None and badge is not None and badge != paid_status:
        c.issues.append(
            Issue(rule="paid_status_conflict", field="payment.paid_status", expected=str(badge), observed=str(paid_status))
        )
    payment_date = c.parse(raw.payment_date, "payment.payment_date", parse_date, required=False)

    if not raw.items:
        c.issues.append(Issue(rule="required", field="items", expected="at least one item", observed="0 items"))
    items = [_item(c, it, i, currency) for i, it in enumerate(raw.items)]

    totals_vals = dict(
        net=c.parse(raw.net_total, "totals.net", parse_money, currency),
        vat=c.parse(raw.vat_total, "totals.vat", parse_money, currency),
        gross=c.parse(raw.gross_total, "totals.gross", parse_money, currency),
    )

    if c.issues:
        return NormalizeResult(spec=None, issues=c.issues)

    assert names is not None and billing is not None and method is not None and paid_status is not None
    order = Order(**order_vals)
    debtor = Debtor(
        company=company,
        alias=c.text(raw.customer_alias, "debtor.alias", required=False),
        contact_first_name=names[0],
        contact_last_name=names[1],
        email=c.text(raw.email, "debtor.email", required=False),
        phone=c.text(raw.phone, "debtor.phone", required=False),
    )
    checked_items = [it for it in items if it is not None]
    totals = Totals(**totals_vals)
    spec = OrderSpec(
        order=order,
        debtor=debtor,
        billing_address=billing,
        delivery_address=delivery,
        payment=Payment(method=method, paid_status=paid_status, payment_date=payment_date),
        items=checked_items,
        totals=totals,
        derived=compute_derived(
            method=method, billing=billing, delivery=delivery, items=checked_items, totals=totals
        ),
        provenance=provenance or Provenance(),
    )
    return NormalizeResult(spec=spec)
