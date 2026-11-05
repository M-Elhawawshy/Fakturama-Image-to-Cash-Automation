"""Part H: the Debtor plan, read-back rules, Debtors-list decision, validation marks, recorded editor."""

from pathlib import Path

import pytest
from PIL import Image

from fakturama_agent.flow.create_debtor import (
    DELIVERY,
    INVOICE,
    MAIN_TAB,
    SECOND_TAB,
    debtor_fields,
    debtor_plan,
    plan_keys,
    roles_in,
    saved_title,
    spec_keys,
    view_decision,
)
from fakturama_agent.flow.create_master import verify_fields
from fakturama_agent.flow.match import DebtorKey, delivery_shows
from fakturama_agent.spec.models import OrderSpec
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.marks import has_error_mark
from fakturama_agent.uia.tree import Snapshot

FIX = Path(__file__).parent / "fixtures"
REG = Registry.load()
SPEC = OrderSpec.model_validate_json((FIX.parent / "golden" / "order_001.json").read_text(encoding="utf-8"))

# read back from the saved contact CUST000004 on 2026-11-05 (create-debtor --without-email)
SAVED = {
    "Customer ID": "CUST000004", "Company": "Northstar Office GmbH", "First Name": "Marta", "Last Name": "Klein",
    "address tabs": "Main address, additional address #1",
    "Main address/additional name": "", "Main address/Street": "Friedrichstrasse 88", "Main address/ZIP": "10117",
    "Main address/City": "Berlin", "Main address/Country": "Germany", "Main address/E-Mail": "",
    "Main address/Telephone": "+49 30 5550 1420", "Main address/address type": "Invoice address",
    "additional address #1/additional name": "Northstar Office Warehouse",
    "additional address #1/Street": "Beusselstrasse 44", "additional address #1/ZIP": "10553",
    "additional address #1/City": "Berlin", "additional address #1/Country": "Germany",
    "additional address #1/E-Mail": "", "additional address #1/Telephone": "",
    "additional address #1/address type": "Delivery address",
    "Alias name": "NORTHSTAR-BERLIN", "Payment": "Bank Transfer", "Discount": "0%", "Net or Gross": "Net",
}


def test_plan_for_the_sample():
    plan = debtor_plan(SPEC, "Bank Transfer")
    main, second = plan.addresses
    assert (main.tab, main.additional_name, main.street, main.zip, main.email) == (
        MAIN_TAB, "", "Friedrichstrasse 88", "10117", "marta.klein@example.test")
    assert main.roles == {INVOICE} and second.roles == {DELIVERY}
    assert (second.tab, second.additional_name, second.email, second.phone) == (
        SECOND_TAB, "Northstar Office Warehouse", "", "")
    assert plan.alias == "NORTHSTAR-BERLIN" and plan.net_gross == "Net" and plan.notes == ()
    assert saved_title(plan) == "Northstar Office GmbH, Marta Klein"


def test_same_billing_and_delivery_is_one_address_with_both_roles():
    spec = SPEC.model_copy(update={"delivery_address": None})
    plan = debtor_plan(spec, "Bank Transfer")
    assert len(plan.addresses) == 1 and plan.addresses[0].roles == {INVOICE, DELIVERY}


def test_without_email_empties_it_and_says_so():
    plan = debtor_plan(SPEC, "Bank Transfer", keep_email=False)
    assert plan.addresses[0].email == "" and "marta.klein@example.test" in plan.notes[0]


def test_saved_contact_reads_back_clean():
    plan = debtor_plan(SPEC, "Bank Transfer", keep_email=False)
    assert verify_fields(SAVED, debtor_fields(plan, "CUST000004")) == []


@pytest.mark.parametrize(
    "field, value",
    [("Company", ""), ("Main address/E-Mail", "x@y.com"), ("Main address/address type", "Invoice address, Delivery address"),
     ("additional address #1/address type", ""), ("address tabs", "Main address"), ("Payment", "Pay Cash"),
     ("Discount", "5%"), ("Customer ID", "CUST000005")],
)
def test_any_difference_is_reported(field, value):
    plan = debtor_plan(SPEC, "Bank Transfer", keep_email=False)
    problems = verify_fields({**SAVED, field: value}, debtor_fields(plan, "CUST000004"))
    assert len(problems) == 1 and problems[0].startswith(field)


def test_roles_text():
    assert roles_in("Invoice address") == {INVOICE}
    assert roles_in("") == set()
    assert roles_in("Invoice address, Delivery address") == {INVOICE, DELIVERY}


# --- the Debtors list shows the delivery ZIP/City of a contact with two addresses ---------------

ROWS = [
    {"No.": "CUST000001", "First Name": "Alice", "Name": "Smith", "Company": "Learning Labs", "ZIP": "10115", "City": "Berlin"},
    {"No.": "CUST000004", "First Name": "Marta", "Name": "Klein", "Company": "Northstar Office GmbH", "ZIP": "10553",
     "City": "Berlin"},
]


def test_debtors_list_row_with_delivery_zip_is_the_contact():
    d = view_decision(ROWS, spec_keys(SPEC))
    assert d.kind == "select" and d.index == 1 and "delivery" in d.reason
    plan = debtor_plan(SPEC, "Bank Transfer")
    assert view_decision(ROWS, plan_keys(plan)).index == 1


def test_billing_zip_still_matches_and_strangers_conflict():
    billing = [{**ROWS[1], "ZIP": "10117"}]
    assert view_decision(billing, spec_keys(SPEC)).kind == "select"
    other = [{**ROWS[1], "ZIP": "99999"}]
    assert view_decision(other, spec_keys(SPEC)).kind == "conflict"
    assert view_decision(ROWS[:1], spec_keys(SPEC)).kind == "not_found"


def test_two_rows_matching_either_address_are_ambiguous():
    two = [{**ROWS[1], "ZIP": "10117"}, {**ROWS[1], "No.": "CUST000009"}]
    d = view_decision(two, spec_keys(SPEC))
    assert d.kind == "ambiguous" and "CUST000009" in d.reason


# --- the Order's delivery address text ------------------------------------------------------------

SHOWN = "Northstar Office GmbH\r\nMarta Klein\r\nBeusselstrasse 44\r\nDE-10553 Berlin\r\nGermany"  # recorded
KEY = DebtorKey("Northstar Office GmbH", "Marta", "Klein", "10553", "Berlin")


def test_delivery_text_recorded_is_ok():
    assert delivery_shows(SHOWN, KEY, "Beusselstrasse 44") == []


def test_delivery_text_with_the_billing_address_fails():
    billing = "Northstar Office GmbH\r\nMarta Klein\r\nFriedrichstrasse 88\r\nDE-10117 Berlin\r\nGermany"
    assert len(delivery_shows(billing, KEY, "Beusselstrasse 44")) == 2


# --- validation mark --------------------------------------------------------------------------------


def test_error_mark_on_recorded_captures():
    assert has_error_mark(Image.open(FIX / "marks" / "email_invalid.png"))
    assert not has_error_mark(Image.open(FIX / "marks" / "email_valid.png"))


# --- recorded editor --------------------------------------------------------------------------------


def test_recorded_new_debtor_editor():
    snap = Snapshot.load(FIX / "uia" / "editor_debtor_new.json")
    assert REG.resolve(snap, "debtor.customer_id").value == "CUST000004"
    assert REG.resolve(snap, "address.country").value == "United States"
    assert REG.resolve(snap, "address.roles").value in (None, "")
    misc = Snapshot.load(FIX / "uia" / "editor_debtor_misc.json")
    assert REG.resolve(misc, "debtor.payment").value == "Pay Cash"  # the Cash term's description
    assert REG.resolve(misc, "debtor.discount").value == "0%"
    two = Snapshot.load(FIX / "uia" / "editor_debtor_two_addresses.json")
    tabs = [n.name for n in two.root.walk() if n.control_type == "TabItem"]
    assert tabs[:2] == [MAIN_TAB, SECOND_TAB]


def test_roles_popup_recording():
    popup = Snapshot.load(FIX / "uia" / "debtor_roles_popup.json")
    boxes = {n.name: n.toggle for n in popup.root.walk() if n.control_type == "CheckBox"}
    assert boxes == {INVOICE: "off", DELIVERY: "off"}
