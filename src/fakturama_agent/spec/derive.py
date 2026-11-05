"""Values computed in code, never asked of the model.

All rounding is half-up to 2 decimal places, matching how invoices are printed.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from fakturama_agent.spec.models import (
    Address,
    Derived,
    DerivedItem,
    DerivedTotals,
    Item,
    PaymentMethod,
    Totals,
)

CENT = Decimal("0.01")
HUNDRED = Decimal(100)

# Canonical Fakturama payment codes. The dropdown shows "Credit transfer " with a
# trailing space; that is a Phase 3 UI matching concern, not part of the spec.
PAYMENT_CODES: dict[PaymentMethod, str] = {
    PaymentMethod.BANK_TRANSFER: "Credit transfer",
    PaymentMethod.CREDIT_CARD: "Credit card",
    PaymentMethod.SEPA_DIRECT_DEBIT: "SEPA direct debit",
}


def round_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def canonical_number(value: Decimal) -> Decimal:
    """Drop insignificant trailing zeros without switching to exponent form (19.0 -> 19, 7.50 -> 7.5)."""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def vat_name(vat_percent: Decimal) -> str:
    return f"VAT {canonical_number(vat_percent)}%"


def product_gross_price(unit_net_price: Decimal, vat_percent: Decimal) -> Decimal:
    return round_money(unit_net_price * (1 + vat_percent / HUNDRED))


def expected_line_net(quantity: Decimal, unit_net_price: Decimal, discount_percent: Decimal) -> Decimal:
    return round_money(quantity * unit_net_price * (1 - discount_percent / HUNDRED))


def payment_code(method: PaymentMethod) -> str:
    return PAYMENT_CODES[method]


def vat_total(lines: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Sum of per-rate VAT: for each rate, round(rate% x sum of net bases). ``lines`` = (vat_percent, line_net)."""
    bases: dict[Decimal, Decimal] = {}
    for rate, net in lines:
        key = canonical_number(rate)
        bases[key] = bases.get(key, Decimal(0)) + net
    return sum((round_money(base * rate / HUNDRED) for rate, base in bases.items()), Decimal("0.00"))


def billing_equals_delivery(billing: Address, delivery: Address | None) -> bool:
    return delivery is None or billing == delivery


def compute_derived(
    *,
    method: PaymentMethod,
    billing: Address,
    delivery: Address | None,
    items: list[Item],
    totals: Totals,
) -> Derived:
    derived_items = [
        DerivedItem(
            line_no=it.line_no,
            vat_name=vat_name(it.vat_percent),
            product_gross_price=product_gross_price(it.unit_net_price, it.vat_percent),
            expected_line_net=expected_line_net(it.quantity, it.unit_net_price, it.discount_percent),
        )
        for it in items
    ]
    net = sum((d.expected_line_net for d in derived_items), Decimal("0.00"))
    vat = vat_total((it.vat_percent, d.expected_line_net) for it, d in zip(items, derived_items, strict=True))
    gross = net + vat
    return Derived(
        payment_code=payment_code(method),
        billing_equals_delivery=billing_equals_delivery(billing, delivery),
        items=derived_items,
        totals=DerivedTotals(
            net=net,
            vat=vat,
            gross=gross,
            matches_printed=(net, vat, gross) == (totals.net, totals.vat, totals.gross),
        ),
    )
