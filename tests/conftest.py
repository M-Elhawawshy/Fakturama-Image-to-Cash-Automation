from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakturama_agent.spec.models import OrderSpec, RawExtraction

TESTS = Path(__file__).parent
GOLDEN = TESTS / "golden" / "order_001.json"
RAW_FIXTURE = TESTS / "fixtures" / "raw_order_001.json"


@pytest.fixture
def golden_dict() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def golden_spec() -> OrderSpec:
    return OrderSpec.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def raw_fixture() -> RawExtraction:
    return RawExtraction.model_validate_json(RAW_FIXTURE.read_text(encoding="utf-8"))
