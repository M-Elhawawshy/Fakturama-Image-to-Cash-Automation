"""Offline tests for the Gemini client, using a fake client (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.genai import errors, types

from fakturama_agent.extract.llm import (
    DEFAULT_MODEL_ID,
    PROMPT,
    ExtractionError,
    GeminiConfig,
    GeminiExtractor,
    gemini_response_schema,
    mime_type_for,
    sha256_hex,
)
from fakturama_agent.spec.models import RawExtraction, RawItem

from .conftest import RAW_FIXTURE

IMAGE = b"\x89PNG fake image bytes"


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, *outcomes):
        self.models = FakeModels(outcomes)


def _error(code: int, retry_delay: str | None = None) -> errors.APIError:
    status = {429: "RESOURCE_EXHAUSTED", 400: "INVALID_ARGUMENT", 503: "UNAVAILABLE"}.get(code, "ERROR")
    body = {"error": {"code": code, "message": "boom", "status": status}}
    if retry_delay:
        body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, body)


@pytest.fixture
def fixture_text() -> str:
    return RAW_FIXTURE.read_text(encoding="utf-8")


def _extractor(tmp_path: Path, client, **config) -> tuple[GeminiExtractor, list[float]]:
    sleeps: list[float] = []
    cfg = GeminiConfig(cache_dir=tmp_path / "cache", **config)
    return GeminiExtractor(config=cfg, client=client, sleep=sleeps.append), sleeps


def test_request_shape(tmp_path, fixture_text):
    client = FakeClient(fixture_text)
    extractor, _ = _extractor(tmp_path, client)
    result = extractor.extract(IMAGE, "image/png")

    assert result.raw == RawExtraction.model_validate_json(fixture_text)
    assert result.model_id == DEFAULT_MODEL_ID
    assert result.image_sha256 == sha256_hex(IMAGE)
    assert result.from_cache is False

    (call,) = client.models.calls
    assert call["model"] == DEFAULT_MODEL_ID
    image_part, prompt = call["contents"]
    assert isinstance(image_part, types.Part)
    assert image_part.inline_data.data == IMAGE
    assert image_part.inline_data.mime_type == "image/png"
    assert prompt == PROMPT
    config = call["config"]
    assert config.temperature == 0
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == gemini_response_schema()


def _keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | set().union(*(_keys(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_keys(v) for v in node)) if node else set()
    return set()


def test_gemini_schema_is_self_contained_and_complete():
    schema = gemini_response_schema()
    assert not _keys(schema) & {"$ref", "$defs", "additionalProperties", "title", "default"}
    assert set(schema["properties"]) == set(RawExtraction.model_fields)
    item_schema = schema["properties"]["items"]["items"]
    assert item_schema["type"] == "object"
    assert set(item_schema["properties"]) == set(RawItem.model_fields)
    assert schema["properties"]["company"] =={"anyOf": [{"type": "string"}, {"type": "null"}]}


def test_cache_hit_skips_api(tmp_path, fixture_text):
    client = FakeClient(fixture_text)
    extractor, _ = _extractor(tmp_path, client)
    first = extractor.extract(IMAGE, "image/png")
    second = extractor.extract(IMAGE, "image/png")
    assert len(client.models.calls) == 1
    assert second.from_cache is True
    assert second.raw == first.raw

    entry = json.loads(extractor.cache_path(sha256_hex(IMAGE)).read_text(encoding="utf-8"))
    assert entry["model_id"] == DEFAULT_MODEL_ID
    assert entry["image_sha256"] == sha256_hex(IMAGE)
    assert entry["response_text"] == fixture_text


def test_cache_is_keyed_by_image_and_model(tmp_path, fixture_text):
    client = FakeClient(fixture_text, fixture_text, fixture_text)
    a, _ = _extractor(tmp_path, client)
    a.extract(IMAGE, "image/png")
    a.extract(IMAGE + b"!", "image/png")  # different image -> miss
    b, _ = _extractor(tmp_path, client, model_id="gemini-other")
    b.extract(IMAGE, "image/png")  # different model -> miss
    assert len(client.models.calls) == 3


def test_cache_disabled(tmp_path, fixture_text):
    client = FakeClient(fixture_text, fixture_text)
    extractor = GeminiExtractor(config=GeminiConfig(cache_dir=None), client=client)
    extractor.extract(IMAGE, "image/png")
    extractor.extract(IMAGE, "image/png")
    assert len(client.models.calls) == 2


def test_retries_429_with_backoff_then_succeeds(tmp_path, fixture_text):
    client = FakeClient(_error(429), _error(503), fixture_text)
    extractor, sleeps = _extractor(tmp_path, client, base_delay_s=1.0)
    assert extractor.extract(IMAGE, "image/png").raw.external_reference == "WEB-2026-0714-A17"
    assert len(client.models.calls) == 3
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 1.5  # base * 2^0 + jitter
    assert 2.0 <= sleeps[1] <= 2.5  # base * 2^1 + jitter


def test_retry_honours_server_retry_delay(tmp_path, fixture_text):
    client = FakeClient(_error(429, retry_delay="7s"), fixture_text)
    extractor, sleeps = _extractor(tmp_path, client, base_delay_s=1.0)
    extractor.extract(IMAGE, "image/png")
    assert 7.0 <= sleeps[0] <= 7.5


def test_backoff_is_capped(tmp_path, fixture_text):
    client = FakeClient(_error(429, retry_delay="600s"), fixture_text)
    extractor, sleeps = _extractor(tmp_path, client, max_delay_s=30.0)
    extractor.extract(IMAGE, "image/png")
    assert sleeps == [30.0]


def test_gives_up_after_max_attempts(tmp_path):
    client = FakeClient(*[_error(429)] * 3)
    extractor, sleeps = _extractor(tmp_path, client, max_attempts=3)
    with pytest.raises(ExtractionError, match="after 3 attempts"):
        extractor.extract(IMAGE, "image/png")
    assert len(client.models.calls) == 3
    assert len(sleeps) == 2


def test_non_retryable_error_fails_fast(tmp_path):
    client = FakeClient(_error(400))
    extractor, sleeps = _extractor(tmp_path, client)
    with pytest.raises(ExtractionError, match="400"):
        extractor.extract(IMAGE, "image/png")
    assert sleeps == []


@pytest.mark.parametrize("text", ["not json", '{"items": "nope"}', '{"unexpected_field": "x"}', ""])
def test_invalid_response_is_error_and_not_cached(tmp_path, text):
    client = FakeClient(text)
    extractor, _ = _extractor(tmp_path, client)
    with pytest.raises(ExtractionError):
        extractor.extract(IMAGE, "image/png")
    assert not extractor.cache_path(sha256_hex(IMAGE)).exists()


def test_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    extractor = GeminiExtractor(config=GeminiConfig(cache_dir=tmp_path))
    with pytest.raises(ExtractionError, match="GEMINI_API_KEY"):
        extractor.extract(IMAGE, "image/png")


def test_model_id_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    assert GeminiConfig.from_env().model_id == "gemini-test-model"
    monkeypatch.delenv("GEMINI_MODEL")
    assert GeminiConfig.from_env().model_id == DEFAULT_MODEL_ID


def test_mime_type_for():
    assert mime_type_for(Path("a.PNG")) == "image/png"
    assert mime_type_for(Path("a.jpeg")) == "image/jpeg"
    with pytest.raises(ExtractionError):
        mime_type_for(Path("a.gif"))


def test_offline_guard_blocks_real_client(tmp_path, monkeypatch):
    """Sanity check: a real client in an offline test cannot reach the network."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-offline-key")
    extractor = GeminiExtractor(config=GeminiConfig(cache_dir=tmp_path, max_attempts=1))
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        extractor.extract(IMAGE, "image/png")
