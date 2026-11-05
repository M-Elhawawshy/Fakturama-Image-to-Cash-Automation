import datetime as dt
from decimal import Decimal as D

from fakturama_agent.extract.validate import validate
from fakturama_agent.spec.derive import compute_derived
from fakturama_agent.spec.models import OrderSpec, PaidStatus


def _rederive(spec: OrderSpec) -> OrderSpec:
    """Recompute the derived block so each corrupted copy trips exactly one rule."""
    derived = compute_derived(
        method=spec.payment.method,
        billing=spec.billing_address,
        delivery=spec.delivery_address,
        items=spec.items,
        totals=spec.totals,
    )
    return spec.model_copy(update={"derived": derived})


def _with_item(spec: OrderSpec, index: int, **changes) -> OrderSpec:
    items = list(spec.items)
    items[index] = items[index].model_copy(update=changes)
    return _rederive(spec.model_copy(update={"items": items}))


def _with_totals(spec: OrderSpec, **changes) -> OrderSpec:
    return _rederive(spec.model_copy(update={"totals": spec.totals.model_copy(update=changes)}))


def _rules(spec: OrderSpec) -> list[str]:
    return [i.rule for i in validate(spec)]


def test_golden_has_no_issues(golden_spec):
    assert validate(golden_spec) == []


def test_wrong_line_net(golden_spec):
    # Printed line net misread (450.00 -> 540.00); net total no longer adds up either.
    issues = validate(_with_item(golden_spec, 0, line_net=D("540.00")))
    line_issues = [i for i in issues if i.rule == "line_net"]
    assert len(line_issues) == 1
    assert (line_issues[0].field, line_issues[0].expected, line_issues[0].observed) == (
        "items[0].line_net",
        "450.00",
        "540.00",
    )


def test_wrong_net_total(golden_spec):
    assert _rules(_with_totals(golden_spec, net=D("575.00"), gross=D("683.30"))) == ["net_total"]


def test_wrong_vat_total(golden_spec):
    assert _rules(_with_totals(golden_spec, vat=D("108.03"), gross=D("678.03"))) == ["vat_total"]


def test_wrong_gross_total(golden_spec):
    assert _rules(_with_totals(golden_spec, gross=D("687.30"))) == ["gross_total"]


def test_duplicate_sku(golden_spec):
    assert _rules(_with_item(golden_spec, 1, sku="CHR-ERG-01")) == ["sku_unique"]


def test_empty_sku(golden_spec):
    assert _rules(_with_item(golden_spec, 1, sku="  ")) == ["sku_required"]


def test_paid_without_payment_date(golden_spec):
    payment = golden_spec.payment.model_copy(update={"payment_date": None})
    assert _rules(golden_spec.model_copy(update={"payment": payment})) == ["payment_date_required"]


def test_unpaid_with_payment_date(golden_spec):
    payment = golden_spec.payment.model_copy(update={"paid_status": PaidStatus.UNPAID})
    assert _rules(golden_spec.model_copy(update={"payment": payment})) == ["payment_date_unexpected"]


def test_unpaid_without_date_is_fine(golden_spec):
    payment = golden_spec.payment.model_copy(update={"paid_status": PaidStatus.UNPAID, "payment_date": None})
    assert validate(golden_spec.model_copy(update={"payment": payment})) == []


def test_missing_currency(golden_spec):
    order = golden_spec.order.model_copy(update={"currency": ""})
    assert _rules(golden_spec.model_copy(update={"order": order})) == ["currency"]


def test_required_field_blank(golden_spec):
    debtor = golden_spec.debtor.model_copy(update={"company": " "})
    issues = validate(golden_spec.model_copy(update={"debtor": debtor}))
    assert [(i.rule, i.field) for i in issues] == [("required", "debtor.company")]


def test_no_items(golden_spec):
    assert "required" in _rules(_rederive(golden_spec.model_copy(update={"items": []})))


def test_stale_derived_block(golden_spec):
    # Hand-edited VAT rate without recomputing derived (gross price, VAT name).
    items = list(golden_spec.items)
    items[1] = items[1].model_copy(update={"vat_percent": D("7")})
    assert "derived_stale" in _rules(golden_spec.model_copy(update={"items": items}))


def test_mangled_sku_is_not_caught_by_arithmetic(golden_spec):
    """Expected pass: an O/0 swap in a SKU leaves every sum intact.

    This documents the gap the step-5 OCR cross-check exists to close: the
    validator alone cannot tell CHR-ERG-01 from CHR-ERG-O1.
    """
    assert validate(_with_item(golden_spec, 0, sku="CHR-ERG-O1")) == []


def test_dates_are_typed(golden_spec):
    assert golden_spec.order.order_date == dt.date(2026, 7, 14)
    assert golden_spec.payment.payment_date == dt.date(2026, 7, 18)
