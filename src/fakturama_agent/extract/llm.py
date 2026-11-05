"""Vision LLM pass: image bytes -> RawExtraction (values copied verbatim).

Gemini Flash with a JSON-schema structured output (the RawExtraction model),
temperature 0, retry with exponential backoff on 429/5xx, and a disk cache so
the same image + model + prompt is only ever paid for once.

The model is only asked to *copy* what is printed. Everything that needs a
computation or interpretation happens later in normalize/derive/validate.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from fakturama_agent.spec.models import RawExtraction

DEFAULT_MODEL_ID = "gemini-3.5-flash-lite"  # stable Flash-Lite id (user choice, 2026-11-05); override with GEMINI_MODEL
DEFAULT_CACHE_DIR = Path(".cache") / "gemini"
RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})

PROMPT = """\
You transcribe a sales order image into JSON. Copy every value exactly as printed:
same characters, same case, same punctuation, same number formatting, including
any currency code or % sign that is printed next to the value.

Rules:
- Never compute, convert, round, translate, correct or reformat anything.
- If a field is not printed on the image, return null. Never infer or guess a value.
- Identifiers (references, SKUs, customer IDs, ZIP codes, email) must be copied
  character by character. Do not "fix" characters that look unusual.
- status_badge: the standalone status badge/stamp, if any (e.g. top right corner).
  paid_status: the value printed under a "paid status" label.
- Addresses: split the printed lines into name, street (with house number), zip,
  city and country. Do not merge or drop lines.
- contact_name: the full contact name as one string.
- items: one entry per printed table row that has content, in printed order. Skip
  empty rows. line_no is the printed row number; discount and vat_rate as printed
  (e.g. "10%"); line_net is the printed line total.
- net_total, vat_total, gross_total: the printed totals, including any currency code.
"""

PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:12]

MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class ExtractionError(RuntimeError):
    """The LLM pass could not produce a schema-valid RawExtraction (manual review)."""


@dataclass(frozen=True)
class LLMResult:
    raw: RawExtraction
    model_id: str
    image_sha256: str
    from_cache: bool


class VisionExtractor(Protocol):
    """Provider interface, so the model behind the extractor can be swapped."""

    def extract(self, image: bytes, mime_type: str) -> LLMResult: ...


@dataclass(frozen=True)
class GeminiConfig:
    model_id: str = DEFAULT_MODEL_ID
    temperature: float = 0.0
    max_attempts: int = 5
    base_delay_s: float = 2.0
    max_delay_s: float = 60.0
    cache_dir: Path | None = DEFAULT_CACHE_DIR  # None disables the cache

    @classmethod
    def from_env(cls, **overrides: Any) -> GeminiConfig:
        model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL_ID
        return cls(**{"model_id": model, **overrides})


def mime_type_for(path: Path) -> str:
    try:
        return MIME_TYPES[path.suffix.lower()]
    except KeyError:
        raise ExtractionError(f"unsupported image type {path.suffix!r}; expected one of {sorted(MIME_TYPES)}") from None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_DROPPED_SCHEMA_KEYS = frozenset({"additionalProperties", "title", "default"})


def gemini_response_schema() -> dict[str, Any]:
    """RawExtraction's JSON schema reduced to what Gemini accepts.

    ``$ref``s are inlined (no ``$defs``), and ``additionalProperties`` (from
    ``extra="forbid"``), titles and defaults are dropped. Local parsing still
    uses the strict pydantic model, so unknown fields are still rejected.
    """
    schema = RawExtraction.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: clean(v) for k, v in node.items() if k not in _DROPPED_SCHEMA_KEYS}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema)


def _retry_delay_hint(error: Exception) -> float | None:
    """Server-suggested delay from a google.rpc.RetryInfo detail, e.g. '7s'."""
    details = getattr(error, "details", None)
    try:
        entries = details["error"]["details"]  # type: ignore[index]
    except (KeyError, TypeError):
        return None
    for entry in entries or []:
        delay = entry.get("retryDelay") if isinstance(entry, dict) else None
        if delay and (m := re.fullmatch(r"(\d+(?:\.\d+)?)s", delay)):
            return float(m[1])
    return None


@dataclass
class GeminiExtractor:
    config: GeminiConfig = field(default_factory=GeminiConfig)
    client: Any = None  # google.genai.Client or a test double with .models.generate_content
    sleep: Callable[[float], None] = time.sleep

    # -- public ---------------------------------------------------------------

    def extract(self, image: bytes, mime_type: str) -> LLMResult:
        digest = sha256_hex(image)
        cached = self._cache_read(digest)
        if cached is not None:
            return LLMResult(raw=cached, model_id=self.config.model_id, image_sha256=digest, from_cache=True)

        text = self._call_with_retry(image, mime_type)
        raw = self._parse(text)
        self._cache_write(digest, text)
        return LLMResult(raw=raw, model_id=self.config.model_id, image_sha256=digest, from_cache=False)

    def extract_file(self, path: Path) -> LLMResult:
        return self.extract(path.read_bytes(), mime_type_for(path))

    # -- Gemini call ----------------------------------------------------------

    def _client(self) -> Any:
        if self.client is None:
            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise ExtractionError("GEMINI_API_KEY is not set (put it in .env)")
            from google import genai

            self.client = genai.Client(api_key=key)
        return self.client

    def _request_config(self) -> Any:
        from google.genai import types

        return types.GenerateContentConfig(
            temperature=self.config.temperature,
            response_mime_type="application/json",
            response_json_schema=gemini_response_schema(),
        )

    def _call_once(self, image: bytes, mime_type: str) -> str:
        from google.genai import types

        response = self._client().models.generate_content(
            model=self.config.model_id,
            contents=[types.Part.from_bytes(data=image, mime_type=mime_type), PROMPT],
            config=self._request_config(),
        )
        text = getattr(response, "text", None)
        if not text:
            reason = None
            candidates = getattr(response, "candidates", None) or []
            if candidates:
                reason = getattr(candidates[0], "finish_reason", None)
            raise ExtractionError(f"empty response from {self.config.model_id} (finish_reason={reason})")
        return text

    def _call_with_retry(self, image: bytes, mime_type: str) -> str:
        from google.genai import errors

        for attempt in range(1, self.config.max_attempts + 1):
            try:
                return self._call_once(image, mime_type)
            except errors.APIError as e:
                if e.code not in RETRYABLE_CODES:
                    raise ExtractionError(f"Gemini API error {e.code} {e.status}: {e.message}") from e
                if attempt == self.config.max_attempts:
                    raise ExtractionError(
                        f"Gemini API error {e.code} {e.status} after {attempt} attempts: {e.message}"
                    ) from e
                self.sleep(self._backoff(attempt, e))
        raise AssertionError("unreachable")  # pragma: no cover

    def _backoff(self, attempt: int, error: Exception) -> float:
        hint = _retry_delay_hint(error)
        exp = self.config.base_delay_s * 2 ** (attempt - 1)
        delay = max(hint or 0.0, exp) + random.uniform(0, self.config.base_delay_s / 2)
        return min(delay, self.config.max_delay_s)

    @staticmethod
    def _parse(text: str) -> RawExtraction:
        try:
            return RawExtraction.model_validate_json(text)
        except ValidationError as e:
            raise ExtractionError(f"response does not match the RawExtraction schema: {e}") from e

    # -- cache ----------------------------------------------------------------

    def cache_path(self, image_sha256: str) -> Path | None:
        if self.config.cache_dir is None:
            return None
        model = re.sub(r"[^A-Za-z0-9._-]", "_", self.config.model_id)
        return self.config.cache_dir / f"{image_sha256}.{model}.{PROMPT_VERSION}.json"

    def _cache_read(self, digest: str) -> RawExtraction | None:
        path = self.cache_path(digest)
        if path is None or not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        return self._parse(entry["response_text"])

    def _cache_write(self, digest: str, text: str) -> None:
        path = self.cache_path(digest)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "image_sha256": digest,
            "model_id": self.config.model_id,
            "prompt_version": PROMPT_VERSION,
            "response_text": text,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
