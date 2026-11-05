import datetime as dt
from decimal import Decimal as D

import pytest

from fakturama_agent.extract.normalize import (
    ParseError,
    map_country,
    map_paid_status,
    map_payment_method,
    normalize,
    parse_date,
    parse_decimal,
    parse_money,
    parse_percent,
    split_contact_name,
)
from fakturama_agent.spec.models import PaidStatus, PaymentMethod, RawItem


# --- dates -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2026-07-14", dt.date(2026, 7, 14)), (" 2026-07-18 ", dt.date(2026, 7, 18)), ("14.07.2026", dt.date(2026, 7, 14))],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["07/14/2026", "14/07/2026", "2026-02-30", "2026-7-14", "July 14", ""])
def test_parse_date_rejects_ambiguous_or_invalid(text):
    with pytest.raises(ParseError):
        parse_date(text)


# --- numbers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("250.00", "250.00"), ("40.00", "40.00"), ("1,234.50", "1234.50"), ("2", "2")],
)
def test_parse_decimal_keeps_printed_precision(text, expected):
    assert str(parse_decimal(text)) == expected


@pytest.mark.parametrize("text", ["250,00", "1,234", "1.234,50", "12a", "", "1e3"])
def test_parse_decimal_rejects_ambiguous(text):
    with pytest.raises(ParseError):
        parse_decimal(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("EUR 570.00", "570.00"), ("570.00 EUR", "570.00"), ("€570.00", "570.00"), ("108.30", "108.30")],
)
def test_parse_money_strips_currency(text, expected):
    assert str(parse_money(text, "EUR")) == expected


@pytest.mark.parametrize("text", ["USD 570.00", "EUR 570.00 EUR", "EUR"])
def test_parse_money_rejects_wrong_currency_or_shape(text):
    with pytest.raises(ParseError):
        parse_money(text, "EUR")


@pytest.mark.parametrize(("text", "expected"), [("10%", "10"), ("0%", "0"), ("19 %", "19"), ("19.0%", "19"), ("5.5%", "5.5")])
def test_parse_percent(text, expected):
    assert str(parse_percent(text)) == expected


@pytest.mark.parametrize("text", ["101%", "-5%", "ten%"])
def test_parse_percent_rejects(text):
    with pytest.raises(ParseError):
        parse_percent(text)


# --- names, enums, countries -----------------------------------------------


def test_split_contact_name():
    assert split_contact_name("Marta Klein") == ("Marta", "Klein")
    assert split_contact_name("  Marta   Klein ") == ("Marta", "Klein")


@pytest.mark.parametrize("text", ["Anna Maria Klein", "Klein", "Dr. Marta Klein"])
def test_split_contact_name_needs_exactly_two_tokens(text):
    with pytest.raises(ParseError):
        split_contact_name(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("PAID", PaidStatus.PAID), ("paid", PaidStatus.PAID), (" Paid ", PaidStatus.PAID), ("UNPAID", PaidStatus.UNPAID),
     ("PARTIAL", PaidStatus.UNPAID), ("not paid", PaidStatus.UNPAID)],
)
def test_map_paid_status(text, expected):
    assert map_paid_status(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Bank Transfer", PaymentMethod.BANK_TRANSFER), ("bank  transfer", PaymentMethod.BANK_TRANSFER),
     ("Credit Card", PaymentMethod.CREDIT_CARD), ("SEPA Direct Debit", PaymentMethod.SEPA_DIRECT_DEBIT)],
)
def test_map_payment_method(text, expected):
    assert map_payment_method(text) is expected


@pytest.mark.parametrize("text", ["PayPal", "Wire", "Bank-Transfer", "Cash"])
def test_map_payment_method_unknown(text):
    with pytest.raises(ParseError):
        map_payment_method(text)


def test_map_country():
    assert map_country("Germany") == ("Germany", "DE")
    assert map_country("DE") == ("Germany", "DE")
    with pytest.raises(ParseError):
        map_country("Atlantis")


# --- normalize() end to end on modified fixtures ---------------------------


def _issues(raw):
    result = normalize(raw)
    assert result.spec is None
    return [(i.rule, i.field) for i in result.issues]


def test_normalize_fixture_ok(raw_fixture):
    result = normalize(raw_fixture)
    assert result.ok
    spec = result.spec
    assert spec.order.order_date == dt.date(2026, 7, 14)
    assert (spec.debtor.contact_first_name, spec.debtor.contact_last_name) == ("Marta", "Klein")
    assert spec.items[0].discount_percent == D("10")
    assert str(spec.totals.net) == "570.00"


def test_normalize_three_token_name_is_issue(raw_fixture):
    raw = raw_fixture.model_copy(update={"contact_name": "Marta Anna Klein"})
    assert _issues(raw) == [("split_contact_name", "debtor.contact_name")]


def test_normalize_unknown_payment_method_is_issue(raw_fixture):
    raw = raw_fixture.model_copy(update={"payment_method": "PayPal"})
    assert _issues(raw) == [("map_payment_method", "payment.method")]


@pytest.mark.parametrize("status", ["UNPAID", "PARTIAL"])
def test_normalize_non_paid_status_maps_to_unpaid(raw_fixture, status):
    raw = raw_fixture.model_copy(update={"paid_status": status, "status_badge": status, "payment_date": None})
    assert normalize(raw).spec.payment.paid_status is PaidStatus.UNPAID


def test_normalize_badge_conflict_is_issue(raw_fixture):
    raw = raw_fixture.model_copy(update={"status_badge": "UNPAID"})
    assert _issues(raw) == [("paid_status_conflict", "payment.paid_status")]


def test_normalize_missing_required_field(raw_fixture):
    raw = raw_fixture.model_copy(update={"external_reference": None})
    assert _issues(raw) == [("required", "order.external_reference")]


def test_normalize_collects_all_issues(raw_fixture):
    raw = raw_fixture.model_copy(update={"order_date": "07/14/2026", "gross_total": "USD 678.30"})
    assert _issues(raw) == [("parse_date", "order.order_date"), ("parse_money", "totals.gross")]


def test_normalize_no_delivery_address(raw_fixture):
    blank = {f"delivery_{f}": None for f in ("name", "street", "zip", "city", "country")}
    spec = normalize(raw_fixture.model_copy(update=blank)).spec
    assert spec.delivery_address is None
    assert spec.derived.billing_equals_delivery is True


def test_normalize_partial_delivery_address_is_issue(raw_fixture):
    raw = raw_fixture.model_copy(update={"delivery_zip": None})
    assert _issues(raw) == [("required", "delivery_address.zip")]


def test_normalize_blank_discount_means_zero(raw_fixture):
    items = [raw_fixture.items[0], raw_fixture.items[1].model_copy(update={"discount": None})]
    spec = normalize(raw_fixture.model_copy(update={"items": items})).spec
    assert spec.items[1].discount_percent == D("0")


def test_normalize_bad_item_value(raw_fixture):
    bad = RawItem(**{**raw_fixture.items[0].model_dump(), "quantity": "two"})
    raw = raw_fixture.model_copy(update={"items": [bad, raw_fixture.items[1]]})
    assert _issues(raw) == [("parse_quantity", "items[0].quantity")]
