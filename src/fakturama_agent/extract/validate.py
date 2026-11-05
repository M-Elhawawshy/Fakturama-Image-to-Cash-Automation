"""Arithmetic and completeness checks on an OrderSpec.

Every rule returns Issues instead of raising, so a review report can list all
problems at once. Arithmetic uses the *printed* values, so each rule pins the
failure to one place (a wrong line net fails ``line_net``, not also the totals).
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from fakturama_agent.spec.derive import compute_derived, expected_line_net, vat_total
from fakturama_agent.spec.models import Issue, OrderSpec, PaidStatus

_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _required(spec: OrderSpec) -> list[Issue]:
    fields: dict[str, str | None] = {
        "order.external_reference": spec.order.external_reference,
        "debtor.company": spec.debtor.company,
        "debtor.contact_first_name": spec.debtor.contact_first_name,
        "debtor.contact_last_name": spec.debtor.contact_last_name,
    }
    addresses = [("billing_address", spec.billing_address)]
    if spec.delivery_address is not None:
        addresses.append(("delivery_address", spec.delivery_address))
    for prefix, addr in addresses:
        for name in ("name", "street", "zip", "city", "country", "country_code"):
            fields[f"{prefix}.{name}"] = getattr(addr, name)
    for i, item in enumerate(spec.items):
        fields[f"items[{i}].description"] = item.description
    issues = [Issue(rule="required", field=f, expected="non-empty value", observed=v) for f, v in fields.items() if _blank(v)]
    if not spec.items:
        issues.append(Issue(rule="required", field="items", expected="at least one item", observed="0 items"))
    return issues


def _currency(spec: OrderSpec) -> list[Issue]:
    if _CURRENCY.match(spec.order.currency or ""):
        return []
    return [Issue(rule="currency", field="order.currency", expected="ISO 4217 code, e.g. EUR", observed=spec.order.currency)]


def _skus(spec: OrderSpec) -> list[Issue]:
    issues = [
        Issue(rule="sku_required", field=f"items[{i}].sku", expected="non-empty SKU", observed=it.sku)
        for i, it in enumerate(spec.items)
        if _blank(it.sku)
    ]
    counts = Counter(it.sku for it in spec.items if not _blank(it.sku))
    issues += [
        Issue(rule="sku_unique", field="items.sku", expected="each SKU once", observed=f"{sku} x{n}")
        for sku, n in counts.items()
        if n > 1
    ]
    line_counts = Counter(it.line_no for it in spec.items)
    issues += [
        Issue(rule="line_no_unique", field="items.line_no", expected="each line number once", observed=f"{no} x{n}")
        for no, n in line_counts.items()
        if n > 1
    ]
    return issues


def _payment_date(spec: OrderSpec) -> list[Issue]:
    p = spec.payment
    if p.paid_status is PaidStatus.PAID and p.payment_date is None:
        return [Issue(rule="payment_date_required", field="payment.payment_date", expected="date when PAID", observed=None)]
    if p.paid_status is not PaidStatus.PAID and p.payment_date is not None:
        return [
            Issue(
                rule="payment_date_unexpected",
                field="payment.payment_date",
                expected=f"no date when {p.paid_status}",
                observed=p.payment_date.isoformat(),
            )
        ]
    return []


def _arithmetic(spec: OrderSpec) -> list[Issue]:
    issues: list[Issue] = []
    for i, it in enumerate(spec.items):
        expected = expected_line_net(it.quantity, it.unit_net_price, it.discount_percent)
        if expected != it.line_net:
            issues.append(Issue(rule="line_net", field=f"items[{i}].line_net", expected=str(expected), observed=str(it.line_net)))

    t = spec.totals
    net_sum = sum((it.line_net for it in spec.items), start=Decimal("0.00"))
    if net_sum != t.net:
        issues.append(Issue(rule="net_total", field="totals.net", expected=str(net_sum), observed=str(t.net)))

    vat = vat_total((it.vat_percent, it.line_net) for it in spec.items)
    if vat != t.vat:
        issues.append(Issue(rule="vat_total", field="totals.vat", expected=str(vat), observed=str(t.vat)))

    if t.net + t.vat != t.gross:
        issues.append(Issue(rule="gross_total", field="totals.gross", expected=str(t.net + t.vat), observed=str(t.gross)))
    return issues


def _derived(spec: OrderSpec) -> list[Issue]:
    """Catch a hand-edited spec whose derived block no longer matches its inputs."""
    fresh = compute_derived(
        method=spec.payment.method,
        billing=spec.billing_address,
        delivery=spec.delivery_address,
        items=spec.items,
        totals=spec.totals,
    )
    if fresh == spec.derived:
        return []
    return [Issue(rule="derived_stale", field="derived", expected=fresh.model_dump_json(), observed=spec.derived.model_dump_json())]


def validate(spec: OrderSpec) -> list[Issue]:
    return [
        *_required(spec),
        *_currency(spec),
        *_skus(spec),
        *_payment_date(spec),
        *_arithmetic(spec),
        *_derived(spec),
    ]
