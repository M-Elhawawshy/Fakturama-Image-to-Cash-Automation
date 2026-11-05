"""End-to-end extract pipeline and CLI with a fake LLM and recorded OCR (offline)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from fakturama_agent.cli import cmd_extract
from fakturama_agent.extract.llm import ExtractionError, LLMResult, sha256_hex
from fakturama_agent.extract.ocr import OcrResult
from fakturama_agent.extract.pipeline import run_extract
from fakturama_agent.spec.models import CheckStatus, OrderSpec, RawExtraction

from .conftest import GOLDEN

SAMPLE = Path(__file__).parents[1] / "samples" / "order_001.png"
OCR_FIXTURE = Path(__file__).parent / "fixtures" / "ocr_order_001.json"


@dataclass
class FakeExtractor:
    raw: RawExtraction | None = None
    error: Exception | None = None
    calls: list[tuple[bytes, str]] = field(default_factory=list)

    def extract(self, image: bytes, mime_type: str) -> LLMResult:
        self.calls.append((image, mime_type))
        if self.error:
            raise self.error
        return LLMResult(raw=self.raw, model_id="fake-model", image_sha256=sha256_hex(image), from_cache=False)


def recorded_ocr(_image: bytes) -> OcrResult:
    return OcrResult.load(OCR_FIXTURE)


def _mangled(raw: RawExtraction) -> RawExtraction:
    items = [raw.items[0].model_copy(update={"sku": "CHR-ERG-O1"}), raw.items[1]]
    return raw.model_copy(update={"items": items, "net_total": "EUR 575.00"})


# --- pipeline --------------------------------------------------------------


def test_pipeline_success_matches_golden(raw_fixture, golden_spec):
    fake = FakeExtractor(raw=raw_fixture)
    outcome = run_extract(SAMPLE, fake, recorded_ocr)

    assert outcome.ok, outcome.issues
    assert fake.calls == [(SAMPLE.read_bytes(), "image/png")]
    prov = outcome.spec.provenance
    assert prov.image_sha256 == sha256_hex(SAMPLE.read_bytes())
    assert prov.model_id == "fake-model"
    assert prov.ocr_engine.startswith("rapidocr-onnxruntime")
    assert len(prov.field_checks) == 42
    assert all(c.status is CheckStatus.CONFIRMED for c in prov.field_checks)
    # Everything except provenance is exactly the golden spec.
    assert outcome.spec.model_copy(update={"provenance": golden_spec.provenance}) == golden_spec


def test_pipeline_collects_issues_from_every_stage(raw_fixture):
    outcome = run_extract(SAMPLE, FakeExtractor(raw=_mangled(raw_fixture)), recorded_ocr)
    assert outcome.spec is None
    rules = [(i.rule, i.field) for i in outcome.issues]
    assert ("ocr_conflict", "items[0].sku") in rules  # cross-check
    assert ("ocr_unconfirmed", "totals.net") in rules  # cross-check
    assert ("net_total", "totals.net") in rules  # arithmetic, still reported


def test_pipeline_normalize_failure_still_reports_ocr(raw_fixture):
    raw = raw_fixture.model_copy(update={"payment_method": "PayPal"})
    outcome = run_extract(SAMPLE, FakeExtractor(raw=raw), recorded_ocr)
    assert ("map_payment_method", "payment.method") in [(i.rule, i.field) for i in outcome.issues]
    assert ("ocr_unconfirmed", "payment.method") in [(i.rule, i.field) for i in outcome.issues]


# --- CLI -------------------------------------------------------------------


def test_cli_extract_success(tmp_path, raw_fixture, capsys):
    out = tmp_path / "out" / "order.json"
    out.parent.mkdir()
    (out.parent / "review.md").write_text("stale", encoding="utf-8")

    assert cmd_extract(SAMPLE, out, FakeExtractor(raw=raw_fixture), recorded_ocr) == 0

    spec = OrderSpec.model_validate_json(out.read_text(encoding="utf-8"))
    golden = OrderSpec.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
    assert spec.model_copy(update={"provenance": golden.provenance}) == golden
    assert not (out.parent / "review.md").exists()  # stale review removed
    assert RawExtraction.model_validate_json((out.parent / "raw.json").read_text(encoding="utf-8")) == raw_fixture
    stdout = capsys.readouterr().out
    assert "WEB-2026-0714-A17" in stdout
    assert "OCR confirmed 42/42" in stdout


def test_cli_extract_review(tmp_path, raw_fixture, capsys):
    out = tmp_path / "order.json"
    out.write_text("{}", encoding="utf-8")  # stale spec from an earlier run

    assert cmd_extract(SAMPLE, out, FakeExtractor(raw=_mangled(raw_fixture)), recorded_ocr) == 2

    assert not out.exists()  # never leave an old spec next to a new review
    review = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert review.startswith("# Manual review required")
    assert "| ocr_conflict | items[0].sku | `CHR-ERG-O1` | `CHR-ERG-01` |" in review
    assert "| net_total | totals.net |" in review
    assert "| items[0].sku | `CHR-ERG-O1` | CONFLICT | `CHR-ERG-01` |" in review
    assert json.loads((tmp_path / "raw.json").read_text(encoding="utf-8"))["items"][0]["sku"] == "CHR-ERG-O1"
    assert "ocr_conflict" in capsys.readouterr().err


def test_cli_extract_review_lists_warnings(tmp_path, raw_fixture):
    raw = _mangled(raw_fixture)
    raw.items[0] = raw.items[0].model_copy(update={"description": "Desk Chair Pro"})
    assert cmd_extract(SAMPLE, tmp_path / "order.json", FakeExtractor(raw=raw), recorded_ocr) == 2
    review = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "## Warnings" in review
    assert "| ocr_unconfirmed | items[0].description |" in review


def test_cli_extract_success_with_warning_still_writes(tmp_path, raw_fixture, capsys):
    items = [raw_fixture.items[0].model_copy(update={"description": "Desk Chair Pro"}), raw_fixture.items[1]]
    raw = raw_fixture.model_copy(update={"items": items})
    assert cmd_extract(SAMPLE, tmp_path / "order.json", FakeExtractor(raw=raw), recorded_ocr) == 0
    assert "warning: [ocr_unconfirmed] items[0].description" in capsys.readouterr().out


@pytest.mark.parametrize("error", [ExtractionError("Gemini API error 503 UNAVAILABLE after 5 attempts")])
def test_cli_extract_operational_error(tmp_path, error, capsys):
    out = tmp_path / "order.json"
    out.write_text("{}", encoding="utf-8")
    assert cmd_extract(SAMPLE, out, FakeExtractor(error=error), recorded_ocr) == 1
    assert not out.exists()
    assert not (tmp_path / "review.md").exists()
    assert "safe to retry" in capsys.readouterr().err


def test_cli_extract_unreadable_image(tmp_path):
    bad = tmp_path / "order.png"
    bad.write_bytes(b"nope")
    assert cmd_extract(bad, tmp_path / "order.json", FakeExtractor(), recorded_ocr) == 1
