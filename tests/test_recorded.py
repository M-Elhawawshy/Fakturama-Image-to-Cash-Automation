"""Offline replay of a real Gemini response (tests/fixtures/gemini_order_001.json).

The recording goes through the real GeminiExtractor (only the HTTP client is
faked), real preprocessing and the recorded RapidOCR output, so this is the
whole pipeline minus the network. Re-record with scripts/record_fixtures.py.

The corrupted cases model plausible LLM misreadings of the same image; every
one of them must end in manual review, never in a spec.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from fakturama_agent.extract.llm import PROMPT_VERSION, GeminiConfig, GeminiExtractor
from fakturama_agent.extract.pipeline import run_extract
from fakturama_agent.extract.preprocess import prepare_image

from .test_llm import FakeClient
from .test_pipeline import SAMPLE, recorded_ocr

RECORDING = Path(__file__).parent / "fixtures" / "gemini_order_001.json"


@pytest.fixture(scope="module")
def recording() -> dict:
    return json.loads(RECORDING.read_text(encoding="utf-8"))


def _run(response_text: str, model_id: str, tmp_path: Path):
    client = FakeClient(response_text)
    extractor = GeminiExtractor(config=GeminiConfig(model_id=model_id, cache_dir=tmp_path), client=client)
    outcome = run_extract(SAMPLE, extractor, recorded_ocr)
    assert len(client.models.calls) == 1
    return outcome


def test_recording_matches_current_prompt(recording):
    assert recording["prompt_version"] == PROMPT_VERSION, (
        "The prompt changed since the Gemini response was recorded; "
        "run `python -m uv run python scripts/record_fixtures.py`."
    )


def test_recording_is_for_the_sample(recording):
    assert recording["image_sha256"] == prepare_image(SAMPLE).source_sha256


def test_recorded_response_yields_golden(recording, tmp_path, golden_spec):
    outcome = _run(recording["response_text"], recording["model_id"], tmp_path)
    assert outcome.issues == []
    assert outcome.warnings == []
    assert outcome.spec.provenance.model_id == recording["model_id"]
    assert outcome.spec.model_copy(update={"provenance": golden_spec.provenance}) == golden_spec


# --- corrupted readings ----------------------------------------------------

Mutation = Callable[[dict], None]


def _set(**values) -> Mutation:
    return lambda d: d.update(values)


def _item(index: int, **values) -> Mutation:
    return lambda d: d["items"][index].update(values)


def _drop_item(index: int) -> Mutation:
    return lambda d: d["items"].pop(index)


CORRUPTIONS: dict[str, tuple[Mutation, set[tuple[str, str]]]] = {
    # identifiers: arithmetic is blind to these, OCR is not
    "sku 0->O": (_item(0, sku="CHR-ERG-O1"), {("ocr_conflict", "items[0].sku")}),
    "sku of row 2 copied from row 1": (_item(1, sku="CHR-ERG-01"), {("sku_unique", "items.sku")}),
    "reference digits swapped": (
        _set(external_reference="WEB-2026-0714-A71"),
        {("ocr_unconfirmed", "order.external_reference")},
    ),
    "zip 0->O": (_set(billing_zip="1O117"), {("ocr_conflict", "billing_address.zip")}),
    "email letters swapped": (_set(email="marta.klien@example.test"), {("ocr_unconfirmed", "debtor.email")}),
    "hallucinated customer id": (_set(customer_id="CUST-1001"), {("ocr_unconfirmed", "order.customer_id")}),
    # numbers: both OCR and arithmetic object
    "line net misread": (
        _item(0, line_net="540.00"),
        {("ocr_unconfirmed", "items[0].line_net"), ("line_net", "items[0].line_net"), ("net_total", "totals.net")},
    ),
    "vat total misread": (
        _set(vat_total="EUR 103.80"),
        {("ocr_unconfirmed", "totals.vat"), ("vat_total", "totals.vat")},
    ),
    "vat rate misread": (
        _item(0, vat_rate="7%"),
        {("ocr_unconfirmed", "items[0].vat_rate"), ("vat_total", "totals.vat")},
    ),
    "discount missed": (_item(0, discount=None), {("line_net", "items[0].line_net")}),
    "row skipped": (_drop_item(1), {("net_total", "totals.net")}),
    # formats and enums
    "date reformatted": (
        _set(order_date="14/07/2026"),
        {("parse_date", "order.order_date"), ("ocr_unconfirmed", "order.order_date")},
    ),
    "unknown payment method": (
        _set(payment_method="PayPal"),
        {("map_payment_method", "payment.method"), ("ocr_unconfirmed", "payment.method")},
    ),
    "three-word contact": (
        _set(contact_name="Marta K. Klein"),
        {("split_contact_name", "debtor.contact_name"), ("ocr_unconfirmed", "debtor.contact_name")},
    ),
    "paid without date": (_set(payment_date=None), {("payment_date_required", "payment.payment_date")}),
    "status contradicts badge": (
        _set(paid_status="UNPAID"),
        {("paid_status_conflict", "payment.paid_status"), ("ocr_unconfirmed", "payment.paid_status")},
    ),
    "company missing": (_set(company=None), {("required", "debtor.company")}),
}


@pytest.mark.parametrize("name", list(CORRUPTIONS))
def test_corrupted_reading_goes_to_review(name, recording, tmp_path):
    mutate, expected = CORRUPTIONS[name]
    data = json.loads(recording["response_text"])
    mutate(data)
    outcome = _run(json.dumps(data), recording["model_id"], tmp_path)

    assert outcome.spec is None, f"{name}: a corrupted reading produced a spec"
    rules = {(i.rule, i.field) for i in outcome.issues}
    assert expected <= rules, f"{name}: missing {expected - rules}; got {sorted(rules)}"
