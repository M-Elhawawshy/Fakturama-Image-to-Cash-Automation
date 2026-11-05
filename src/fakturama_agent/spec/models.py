"""Data contracts of the extractor.

``RawExtraction`` holds values exactly as printed on the image (all strings or
null). It doubles as the LLM response schema in step 4, so it stays inside
Gemini's schema subset: plain strings, nullable, one nested list of objects.

``OrderSpec`` is the typed, validated contract consumed by the UIA driver.
Money and percentages are ``Decimal`` (serialized as JSON strings), dates are
``datetime.date`` (serialized ISO ``YYYY-MM-DD``).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Raw extraction (verbatim strings)
# --------------------------------------------------------------------------


class RawItem(_Strict):
    line_no: str | None = None
    sku: str | None = None
    description: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_net_price: str | None = None
    discount: str | None = None
    vat_rate: str | None = None
    line_net: str | None = None


class RawExtraction(_Strict):
    external_reference: str | None = None
    order_date: str | None = None
    customer_id: str | None = None
    currency: str | None = None
    status_badge: str | None = None

    company: str | None = None
    customer_alias: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None

    billing_name: str | None = None
    billing_street: str | None = None
    billing_zip: str | None = None
    billing_city: str | None = None
    billing_country: str | None = None

    delivery_name: str | None = None
    delivery_street: str | None = None
    delivery_zip: str | None = None
    delivery_city: str | None = None
    delivery_country: str | None = None

    payment_method: str | None = None
    paid_status: str | None = None
    payment_date: str | None = None

    items: list[RawItem] = []

    net_total: str | None = None
    vat_total: str | None = None
    gross_total: str | None = None


# --------------------------------------------------------------------------
# OrderSpec (typed contract)
# --------------------------------------------------------------------------


class PaymentMethod(StrEnum):
    BANK_TRANSFER = "Bank Transfer"
    CREDIT_CARD = "Credit Card"
    SEPA_DIRECT_DEBIT = "SEPA Direct Debit"


class PaidStatus(StrEnum):
    PAID = "PAID"
    UNPAID = "UNPAID"


class Order(_Strict):
    external_reference: str
    order_date: dt.date
    customer_id: str | None = None  # informational only; Fakturama assigns its own
    currency: str


class Debtor(_Strict):
    company: str
    alias: str | None = None
    contact_first_name: str
    contact_last_name: str
    email: str | None = None
    phone: str | None = None


class Address(_Strict):
    name: str
    street: str
    zip: str
    city: str
    country: str  # canonical English name
    country_code: str  # ISO 3166-1 alpha-2


class Payment(_Strict):
    method: PaymentMethod
    paid_status: PaidStatus
    payment_date: dt.date | None = None


class Item(_Strict):
    line_no: int
    sku: str
    description: str
    quantity: Decimal
    unit: str | None = None
    unit_net_price: Decimal
    discount_percent: Decimal
    vat_percent: Decimal
    line_net: Decimal  # as printed on the source


class Totals(_Strict):
    net: Decimal
    vat: Decimal
    gross: Decimal


class DerivedItem(_Strict):
    line_no: int
    vat_name: str
    product_gross_price: Decimal
    expected_line_net: Decimal


class DerivedTotals(Totals):
    matches_printed: bool


class Derived(_Strict):
    payment_code: str
    billing_equals_delivery: bool
    items: list[DerivedItem]
    totals: DerivedTotals


class CheckStatus(StrEnum):
    CONFIRMED = "confirmed"  # OCR shows the same characters
    UNCONFIRMED = "unconfirmed"  # OCR shows nothing comparable
    CONFLICT = "conflict"  # OCR shows a look-alike variant (0/O, 1/l, ...)


class FieldCheck(_Strict):
    """OCR agreement for one value the LLM read."""

    field: str
    value: str
    status: CheckStatus
    critical: bool  # critical values must be confirmed; others only warn
    ocr_text: str | None = None  # the conflicting OCR text, if any


class Provenance(_Strict):
    image_sha256: str | None = None
    model_id: str | None = None
    ocr_engine: str | None = None
    field_checks: list[FieldCheck] = []


class OrderSpec(_Strict):
    schema_version: Literal["1"] = "1"
    order: Order
    debtor: Debtor
    billing_address: Address
    delivery_address: Address | None = None  # None: none printed (same as billing)
    payment: Payment
    items: list[Item]
    totals: Totals
    derived: Derived
    provenance: Provenance = Provenance()


class Issue(_Strict):
    """One reason for manual review."""

    rule: str
    field: str
    expected: str | None = None
    observed: str | None = None

    def __str__(self) -> str:
        return f"[{self.rule}] {self.field}: expected {self.expected!r}, observed {self.observed!r}"
