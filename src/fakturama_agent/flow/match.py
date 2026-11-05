"""Existence decisions for selector results (pure; DESIGN.md: never guess or fuzzy-match).

Debtor identity is Company, First Name, Name (last name), ZIP and City, all
compared exactly (case and spaces included). Product identity is the exact
Item No. Outcomes:
- select: exactly one row matches every identity field;
- not_found: no row matches, and no row shares the Company (debtor) - the
  search hit only unrelated rows;
- conflict: a row has the same Company but differs elsewhere (another contact
  or address of that company): creating a second Debtor or picking this one
  would both be guesses, so it is manual review;
- ambiguous: several rows match exactly: manual review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["select", "not_found", "conflict", "ambiguous"]

DEBTOR_FIELDS = ("Company", "First Name", "Name", "ZIP", "City")


@dataclass(frozen=True)
class DebtorKey:
    company: str
    first_name: str
    last_name: str
    zip: str
    city: str

    def as_row(self) -> dict[str, str]:
        return dict(zip(DEBTOR_FIELDS, (self.company, self.first_name, self.last_name, self.zip, self.city), strict=True))


@dataclass(frozen=True)
class Decision:
    kind: Kind
    index: int | None = None  # row to select when kind == "select"
    reason: str = ""
    rows: list[dict] = field(default_factory=list, compare=False)

    @property
    def needs_review(self) -> bool:
        return self.kind in ("conflict", "ambiguous")


def decide_debtor(rows: list[dict[str, str | None]], key: DebtorKey) -> Decision:
    want = key.as_row()
    exact = [i for i, r in enumerate(rows) if all((r.get(f) or "") == v for f, v in want.items())]
    if len(exact) == 1:
        return Decision("select", exact[0], f"one exact match ({rows[exact[0]].get('No.')})", rows)
    if len(exact) > 1:
        nos = [rows[i].get("No.") for i in exact]
        return Decision("ambiguous", None, f"{len(exact)} contacts match every field: {nos}", rows)
    same_company = [r for r in rows if (r.get("Company") or "") == key.company]
    if same_company:
        diffs = []
        for r in same_company:
            fields = [f for f in DEBTOR_FIELDS if (r.get(f) or "") != want[f]]
            diffs.append(f"{r.get('No.')} differs in {fields}")
        return Decision("conflict", None, f"{key.company!r} exists with other details: {'; '.join(diffs)}", rows)
    return Decision("not_found", None, f"no contact with company {key.company!r} among {len(rows)} search hit(s)", rows)


def decide_product(rows: list[dict[str, str | None]], sku: str) -> Decision:
    exact = [i for i, r in enumerate(rows) if (r.get("Item No.") or "") == sku]
    if len(exact) == 1:
        return Decision("select", exact[0], "one exact Item No.", rows)
    if len(exact) > 1:
        return Decision("ambiguous", None, f"{len(exact)} products have Item No. {sku!r}", rows)
    return Decision("not_found", None, f"no product with Item No. {sku!r} among {len(rows)} search hit(s)", rows)


# --- verification of the Order after a selection ----------------------------------


def address_shows(text: str, key: DebtorKey) -> list[str]:
    """Problems with the Order's invoice address text after selecting ``key`` (empty = ok).

    Recorded (2026-11-05): 'Learning Labs\\r\\nAlice Smith\\r\\n10 Test Street\\r\\nDE-10115 Berlin\\r\\nGermany'.
    """
    lines = [line.strip() for line in re.split(r"\r?\n", text or "")]
    problems = []
    if not lines or lines[0] != key.company:
        problems.append(f"first line {lines[0] if lines else ''!r} is not the company {key.company!r}")
    person = f"{key.first_name} {key.last_name}"
    if person not in lines:
        problems.append(f"no line {person!r}")
    place = re.compile(rf"^(?:[A-Z]{{2}}-)?{re.escape(key.zip)} {re.escape(key.city)}$")
    if not any(place.match(line) for line in lines):
        problems.append(f"no line with {key.zip} {key.city}")
    return problems


def line_added(before: list[dict], after: list[dict], sku: str) -> list[str]:
    """Problems with the Order's item lines after selecting ``sku`` (empty = ok)."""
    problems = []
    if len(after) != len(before) + 1:
        problems.append(f"{len(after) - len(before)} lines were added, expected 1")
    count = sum(1 for r in after if r.get("Item No.") == sku) - sum(1 for r in before if r.get("Item No.") == sku)
    if count != 1:
        problems.append(f"{count} new {sku!r} lines, expected 1")
    if after[: len(before)] != before:
        problems.append("existing lines changed")
    return problems


def delivery_shows(text: str, key: DebtorKey, street: str) -> list[str]:
    """Problems with the Order's delivery address text (empty = ok). Recorded 2026-11-05:
    'Northstar Office GmbH / Marta Klein / Beusselstrasse 44 / DE-10553 Berlin / Germany':
    company and person as on the invoice address, then the delivery street and place
    (``key`` carries the delivery ZIP/City). The address's 'additional name' (e.g.
    'Northstar Office Warehouse') is NOT part of Fakturama's address text; it is
    checked on the contact itself (part H read-back)."""
    zip_code, city = key.zip, key.city
    lines = [line.strip() for line in re.split(r"\r?\n", text or "")]
    problems = [p for p in address_shows(text, key) if not p.startswith("no line with")]
    if street not in lines:
        problems.append(f"no line {street!r}")
    place = re.compile(rf"^(?:[A-Z]{{2}}-)?{re.escape(zip_code)} {re.escape(city)}$")
    if not any(place.match(line) for line in lines):
        problems.append(f"no line with {zip_code} {city}")
    return problems
