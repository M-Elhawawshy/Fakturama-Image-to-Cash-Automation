"""Live Gemini test on the real sample image. Run with: uv run pytest -m live"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from fakturama_agent.extract.llm import GeminiConfig, GeminiExtractor
from fakturama_agent.extract.normalize import normalize
from fakturama_agent.extract.validate import validate
from fakturama_agent.spec.models import Provenance

SAMPLE = Path(__file__).parents[1] / "samples" / "order_001.png"

pytestmark = pytest.mark.live


def test_sample_image_matches_golden(golden_spec, tmp_path):
    load_dotenv()
    # Fresh cache dir: this test must exercise the real API, not a recorded answer.
    extractor = GeminiExtractor(config=GeminiConfig.from_env(cache_dir=tmp_path))
    result = extractor.extract_file(SAMPLE)

    normalized = normalize(result.raw, Provenance(image_sha256=result.image_sha256, model_id=result.model_id))
    assert normalized.issues == []
    assert validate(normalized.spec) == []
    expected = golden_spec.model_copy(update={"provenance": normalized.spec.provenance})
    assert normalized.spec == expected
