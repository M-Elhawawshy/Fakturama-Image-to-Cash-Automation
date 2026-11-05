from decimal import Decimal as D

import pytest

from fakturama_agent.spec.derive import (
    canonical_number,
    compute_derived,
    expected_line_net,
    payment_code,
    product_gross_price,
    round_money,
    vat_name,
    vat_total,
)
from fakturama_agent.spec.models import PaymentMethod


@pytest.mark.parametrize(
    ("net", "vat", "gross"),
    [(D("250.00"), D("19"), D("297.50")), (D("40.00"), D("19"), D("47.60")), (D("9.99"), D("7"), D("10.69"))],
)
def test_product_gross_price(net, vat, gross):
    assert str(product_gross_price(net, vat)) == str(gross)


@pytest.mark.parametrize(
    ("rate", "name"),
    [
        (D("19"), "VAT 19%"),
        (D("19.0"), "VAT 19%"),
        (D("7"), "VAT 7%"),
        (D("0"), "VAT 0%"),
        (D("5.50"), "VAT 5.5%"),
        (D("10"), "VAT 10%"),
    ],
)
def test_vat_name(rate, name):
    assert vat_name(rate) == name


def test_canonical_number_never_uses_exponent():
    assert str(canonical_number(D("100"))) == "100"
    assert str(canonical_number(D("10.00"))) == "10"


@pytest.mark.parametrize(
    ("method", "code"),
    [
        (PaymentMethod.BANK_TRANSFER, "Credit transfer"),
        (PaymentMethod.CREDIT_CARD, "Credit card"),
        (PaymentMethod.SEPA_DIRECT_DEBIT, "SEPA direct debit"),
    ],
)
def test_payment_code(method, code):
    assert payment_code(method) == code


@pytest.mark.parametrize(
    ("qty", "unit", "disc", "line"),
    [
        (D("2"), D("250.00"), D("10"), D("450.00")),
        (D("3"), D("40.00"), D("0"), D("120.00")),
        (D("1"), D("0.05"), D("50"), D("0.03")),  # 0.025 rounds half-up
    ],
)
def test_expected_line_net(qty, unit, disc, line):
    assert expected_line_net(qty, unit, disc) == line


def test_round_money_is_half_up():
    assert round_money(D("0.125")) == D("0.13")
    assert round_money(D("2.675")) == D("2.68")


def test_vat_total_rounds_per_rate_not_per_line():
    # per line: 19% of 0.05 = 0.0095 -> 0.01 each -> 0.02; per rate: 19% of 0.10 = 0.019 -> 0.02
    assert vat_total([(D("19"), D("0.05")), (D("19"), D("0.05"))]) == D("0.02")
    # per line: 19% of 0.03 = 0.0057 -> 0.01 each -> 0.03; per rate: 19% of 0.09 = 0.0171 -> 0.02
    assert vat_total([(D("19"), D("0.03"))] * 3) == D("0.02")
    assert vat_total([(D("19"), D("450.00")), (D("19.0"), D("120.00"))]) == D("108.30")
    assert vat_total([(D("19"), D("100.00")), (D("7"), D("100.00"))]) == D("26.00")


def test_compute_derived_on_golden(golden_spec):
    derived = compute_derived(
        method=golden_spec.payment.method,
        billing=golden_spec.billing_address,
        delivery=golden_spec.delivery_address,
        items=golden_spec.items,
        totals=golden_spec.totals,
    )
    assert derived == golden_spec.derived
    assert [d.product_gross_price for d in derived.items] == [D("297.50"), D("47.60")]
    assert [d.expected_line_net for d in derived.items] == [D("450.00"), D("120.00")]
    assert derived.payment_code == "Credit transfer"
    assert derived.billing_equals_delivery is False
    assert derived.totals.matches_printed is True


def test_billing_equals_delivery_when_identical_or_absent(golden_spec):
    common = dict(method=golden_spec.payment.method, items=golden_spec.items, totals=golden_spec.totals)
    b = golden_spec.billing_address
    assert compute_derived(billing=b, delivery=b.model_copy(), **common).billing_equals_delivery is True
    assert compute_derived(billing=b, delivery=None, **common).billing_equals_delivery is True
    other_zip = b.model_copy(update={"zip": "10118"})
    assert compute_derived(billing=b, delivery=other_zip, **common).billing_equals_delivery is False
