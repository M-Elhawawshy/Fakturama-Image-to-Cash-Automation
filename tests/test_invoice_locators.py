"""Phase 4 part A: Invoice editor locators on the recordings (2026-11-06, New Invoice from PO000006)."""

from pathlib import Path

import pytest

from fakturama_agent.uia.locators import LocatorError, Registry
from fakturama_agent.uia.tree import Snapshot

FIX = Path(__file__).parent / "fixtures" / "uia"
REG = Registry.load()
UNPAID = Snapshot.load(FIX / "invoice_editor.json")
PAID = Snapshot.load(FIX / "invoice_editor_paid.json")

COPIED = {  # what the linked Invoice copied from the saved Order (brief 5.1)
    "order.cust_ref": "WEB-2026-0714-A17",
    "invoice.order_date": "Jul 14, 2026",
    "order.vat_mode": "With VAT",
    "order.price_mode": "Net",
    "order.invoice_address": "Northstar Office GmbH\r\nMarta Klein\r\nFriedrichstrasse 88\r\nDE-10117 Berlin\r\nGermany",
    "order.total_net": "$570.00",
    "order.vat_total": "$108.30",
    "order.total": "$678.30",
    "order.discount": "0%",
    "order.shipping": "Free of shipping costs",
    "order.shipping_amount": "$0.00",
    "invoice.payment": "Bank Transfer",
}


@pytest.mark.parametrize("snap", [UNPAID, PAID], ids=["unpaid", "paid"])
def test_copied_fields_resolve_on_the_invoice(snap):
    assert {k: REG.resolve(snap, k).value for k in COPIED} == COPIED


@pytest.mark.parametrize("snap", [UNPAID, PAID], ids=["unpaid", "paid"])
def test_proposed_number_and_dates(snap):
    assert REG.resolve(snap, "order.no").value == "INV000001"
    assert REG.resolve(snap, "order.date").value == "Nov 6, 2026"  # Invoice Date: today, left unchanged
    assert REG.resolve(snap, "invoice.service_date").value == "Nov 6, 2026"


def test_unpaid_shows_due_days_and_pay_until_only():
    assert REG.resolve(UNPAID, "invoice.paid").toggle == "off"
    assert REG.resolve(UNPAID, "invoice.due_days").value == "0"
    assert REG.resolve(UNPAID, "invoice.pay_until").value == "Nov 6, 2026"
    for key in ("invoice.paid_date", "invoice.paid_value"):
        with pytest.raises(LocatorError):
            REG.resolve(UNPAID, key)


def test_paid_shows_at_and_value_only():
    assert REG.resolve(PAID, "invoice.paid").toggle == "on"
    assert REG.resolve(PAID, "invoice.paid_date").value == "Nov 6, 2026"  # default today
    assert REG.resolve(PAID, "invoice.paid_value").value == "$678.30"  # prefilled with the Total
    for key in ("invoice.due_days", "invoice.pay_until"):
        with pytest.raises(LocatorError):
            REG.resolve(PAID, key)


def test_invoice_follow_ups_are_not_the_order_ones():
    group = REG.resolve(UNPAID, "order.followup")
    buttons = [n.name for n in group.walk() if n.control_type == "Button"]
    assert buttons == ["Delivery", "Invoice Correction", "Dunning"]  # never used (brief 5.7)
    with pytest.raises(LocatorError):
        REG.resolve(UNPAID, "order.followup.invoice")


def test_order_follow_up_invoice_is_inside_the_group():
    order = Snapshot.load(FIX / "order_editor_lines.json")
    button = REG.resolve(order, "order.followup.invoice")
    assert button.name == "Invoice" and button.parent.parent.name == "Create a follow-up document"
    assert "Invoke" in button.patterns
