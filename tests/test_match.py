"""Existence decisions and Order verification (pure)."""

from pathlib import Path

import pytest

from fakturama_agent.flow.match import DebtorKey, address_shows, decide_debtor, decide_product, line_added
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.tables import parse_tsv

SEL = Path(__file__).parent / "fixtures" / "selectors"
REG = Registry.load()
ALICE = DebtorKey("Learning Labs", "Alice", "Smith", "10115", "Berlin")
ROW = parse_tsv((SEL / "copy_address_row.txt").read_bytes().decode(), REG.table("select_address"))[0]


def row(**change):
    return {**ROW, **change}


def test_recorded_address_row_parses():
    assert ROW["No."] == "CUST000001" and ROW["Company"] == "Learning Labs" and ROW["local consul..."] is None


def test_select_exact_debtor():
    d = decide_debtor([row(**{"No.": "CUST000009", "Company": "Other"}), ROW], ALICE)
    assert (d.kind, d.index) == ("select", 1)


def test_not_found_when_no_company_matches():
    d = decide_debtor([row(Company="Learning Labs Berlin")], ALICE)  # a substring hit is not the company
    assert d.kind == "not_found" and not d.needs_review
    assert decide_debtor([], ALICE).kind == "not_found"


@pytest.mark.parametrize(
    "field, value", [("First Name", "Alicia"), ("Name", "Smyth"), ("ZIP", "10117"), ("City", "Munich")]
)
def test_same_company_other_details_is_a_conflict(field, value):
    d = decide_debtor([row(**{field: value})], ALICE)
    assert d.kind == "conflict" and d.needs_review and field in d.reason


@pytest.mark.parametrize("change", [{"Company": "learning labs"}, {"Company": "Learning Labs "}, {"Name": "smith"}])
def test_exact_means_exact(change):
    assert decide_debtor([row(**change)], ALICE).kind != "select"


def test_two_exact_contacts_are_ambiguous():
    d = decide_debtor([ROW, row(**{"No.": "CUST000007"})], ALICE)
    assert d.kind == "ambiguous" and "CUST000007" in d.reason


def test_products():
    rows = [{"Item No.": "SKU-001"}, {"Item No.": "NEW-001"}, {"Item No.": "ORN-001"}]
    assert decide_product(rows, "NEW-001").index == 1
    assert decide_product(rows, "001").kind == "not_found"  # search hits, no exact Item No.
    assert decide_product(rows, "sku-001").kind == "not_found"
    assert decide_product([*rows, {"Item No.": "SKU-001"}], "SKU-001").kind == "ambiguous"


def test_address_text_recorded_live():
    text = "Learning Labs\r\nAlice Smith\r\n10 Test Street\r\nDE-10115 Berlin\r\nGermany"
    assert address_shows(text, ALICE) == []
    assert address_shows(text.replace("DE-", ""), ALICE) == []
    assert address_shows("", ALICE)
    bob = DebtorKey("Nimbus Demo", "Bob", "Learner", "80331", "Munich")
    assert len(address_shows(text, bob)) == 3
    assert address_shows(text.replace("10115", "10117"), ALICE) == ["no line with 10115 Berlin"]


def test_line_added():
    items = parse_tsv((SEL / "copy_order_items.txt").read_bytes().decode(), REG.table("order_items"))
    assert items[0]["Item No."] == "SKU-001" and items[0]["U.Price"] == "USD 11.9"
    assert line_added([], items, "SKU-001") == []
    assert line_added([], items, "SKU-002")  # wrong product
    assert line_added([], items * 2, "SKU-001")  # two lines added
    assert line_added(items, items, "SKU-001")  # nothing added
