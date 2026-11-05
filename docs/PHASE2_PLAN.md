# Phase 2 — Extraction (image → validated OrderSpec)

**Goal:** `python -m fakturama_agent extract samples/order_001.png -o out/order.json` writes a validated `OrderSpec`, or exits with code 2 and writes `review.md`. No Fakturama/UIA code in this phase.

**Current session scope: steps 1–3 only** (no network, no API key needed). Steps 4–7 come later.

## Stack

- Python 3.12, `uv` for the env (plain `pip` fallback)
- pydantic v2, `decimal.Decimal` for all money/percentages (never float)
- Later steps: `google-genai` (Gemini Flash), `rapidocr-onnxruntime`, `pillow`, `python-dotenv`
- pytest

## Target layout

```
pyproject.toml
src/fakturama_agent/
  __init__.py
  __main__.py           # -> cli.main()
  cli.py
  spec/models.py        # RawExtraction (strings as printed) + OrderSpec (typed + derived)
  spec/derive.py        # VAT name, product gross price, payment code, expected line prices, totals
  extract/normalize.py  # RawExtraction -> OrderSpec fields
  extract/validate.py   # arithmetic + completeness rules -> list[Issue]
  extract/llm.py        # (step 4) Gemini client
  extract/ocr.py        # (step 5) RapidOCR
  extract/crosscheck.py # (step 5)
samples/order_001.png
tests/golden/order_001.json
tests/fixtures/raw_order_001.json   # hand-written RawExtraction (stands in for the LLM until step 4)
tests/test_normalize.py, tests/test_derive.py, tests/test_validate.py, tests/test_golden.py
```

## Steps

1. **Scaffold.** pyproject, package skeleton, pytest config, `.gitignore` (`.env`, `.cache/`, `out/`, `.venv/`), `.env.example` with `GEMINI_API_KEY=`. Write `tests/fixtures/raw_order_001.json` (values exactly as printed on the image, all strings) and `tests/golden/order_001.json` (the expected normalized OrderSpec) from the data below.
2. **Models, derive, validate** — pure functions, unit tests first.
3. **Normalizer** — RawExtraction → OrderSpec, then golden test: `normalize(raw_fixture)` == golden, and `validate()` returns no issues.

## Model rules

- `RawExtraction`: flat-ish, every value `str | None`, items a list. This will also be the Gemini response schema later, so keep it to types Gemini's schema subset supports (no unions beyond nullable, no `$ref`-heavy nesting).
- `OrderSpec`: `schema_version: "1"`; typed fields; `derived` block computed by `spec/derive.py`; `provenance` (image sha256, model id — may be null in tests).
- Enums: `PaymentMethod` = Bank Transfer | Credit Card | SEPA Direct Debit (unknown → validation error). `PaidStatus` = PAID | UNPAID (anything other than PAID → UNPAID).
- Payment code mapping: Bank Transfer → `Credit transfer`, Credit Card → `Credit card`, SEPA Direct Debit → `SEPA direct debit`. (Fakturama's dropdown text has a trailing space for `Credit transfer ` — that is a Phase 3 UI concern; keep the canonical value without the space here.)
- Contact name: exactly two tokens → first/last. Anything else → validation issue (manual review), do not guess.
- Customer ID is informational only (Fakturama assigns its own).
- `billing_equals_delivery` compares all address fields; if false, Phase 3 creates a second address with the Delivery role.

## Validation rules (each failure = an Issue with rule, field, expected, observed)

- Per line: qty × unit_net × (1 − disc/100), rounded half-up to 2 dp == printed line net.
- Σ line nets == net total.
- Σ per VAT rate (rate × base, rounded 2 dp) == VAT total.
- net + VAT == gross.
- SKUs unique and non-empty; required fields present; payment_date present iff status is PAID; dates valid; currency present.

## Sample data (samples/order_001.png) — golden values

Order: external_reference `WEB-2026-0714-A17`, order_date `2026-07-14`, customer_id `CUST-1007`, currency `EUR`. Badge top-right: PAID.

Debtor: company `Northstar Office GmbH`, alias `NORTHSTAR-BERLIN`, contact `Marta Klein` (first `Marta`, last `Klein`), email `marta.klein@example.test`, phone `+49 30 5550 1420`.

Billing: `Northstar Office GmbH`, `Friedrichstrasse 88`, `10117`, `Berlin`, `Germany` (DE).
Delivery: `Northstar Office Warehouse`, `Beusselstrasse 44`, `10553`, `Berlin`, `Germany` (DE). → `billing_equals_delivery = false`.

Payment: method `Bank Transfer` (code `Credit transfer`), status `PAID`, payment_date `2026-07-18`.

| # | SKU | Description | Qty | Unit | Unit net | Disc % | VAT % | Line net | Derived: VAT name | Derived: product gross |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CHR-ERG-01 | Ergonomic Desk Chair | 2 | pcs | 250.00 | 10 | 19 | 450.00 | VAT 19% | 297.50 |
| 2 | MAT-DESK-02 | Anti-Fatigue Desk Mat | 3 | pcs | 40.00 | 0 | 19 | 120.00 | VAT 19% | 47.60 |

Totals: net `570.00`, VAT `108.30`, gross `678.30`.

## Tests required in steps 1–3

- derive: gross prices 297.50 / 47.60; VAT name; payment code mapping; expected line prices.
- normalize: dates, `10%`→`10`, `EUR 570.00`→`570.00`, name split (and 3-token name → issue), paid-status mapping (`PAID`, `paid`, `UNPAID`, `PARTIAL`→UNPAID), unknown payment method → issue.
- validate: the golden passes with zero issues; each corrupted copy fails with the right rule: wrong line net, wrong net total, wrong VAT total, duplicate SKU, PAID without payment date, mangled SKU is NOT caught by arithmetic (documents why step 5 OCR exists — mark as expected-pass with a comment).
- golden: normalize(raw fixture) == golden JSON.

## Done for this session

`uv run pytest` green; `python -m fakturama_agent normalize tests/fixtures/raw_order_001.json -o out/order.json` (temporary sub-command until the LLM exists) writes a file equal to the golden one.

## Later (steps 4–7, not now)

4. Gemini client (model id in config — check the current Flash model id; temperature 0; JSON schema from RawExtraction; retry with backoff on 429; cache by image sha256 in `.cache/`).
5. RapidOCR + cross-check of identity tokens with confusable-character detection.
6. `extract` CLI + `review.md`.
7. Golden test with recorded Gemini response; corrupted fixtures; optional live test (`-m live`) on the image plus rotated/downscaled/JPEG variants.
