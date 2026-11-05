"""Runs the real RapidOCR engine on the sample. Local models, so still offline."""

from pathlib import Path

import pytest

from fakturama_agent.extract.crosscheck import crosscheck
from fakturama_agent.extract.ocr import run_ocr

SAMPLE = Path(__file__).parents[1] / "samples" / "order_001.png"


@pytest.fixture(scope="module")
def sample_ocr():
    return run_ocr(SAMPLE.read_bytes())


def test_engine_reads_identity_tokens(sample_ocr):
    texts = {b.text for b in sample_ocr.boxes}
    assert {"WEB-2026-0714-A17", "CHR-ERG-01", "MAT-DESK-02", "CUST-1007", "2026-07-18"} <= texts
    assert sample_ocr.engine.startswith("rapidocr-onnxruntime ")
    assert all(0 <= b.score <= 1 and b.box[0] <= b.box[2] and b.box[1] <= b.box[3] for b in sample_ocr.boxes)


def test_live_ocr_confirms_raw_fixture(raw_fixture, sample_ocr):
    assert crosscheck(raw_fixture, sample_ocr).issues == []
