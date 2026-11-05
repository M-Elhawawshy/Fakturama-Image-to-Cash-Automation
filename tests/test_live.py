"""Live end-to-end runs: real Gemini + real RapidOCR on the sample and variants.

Run with: python -m uv run pytest -m live   (needs GEMINI_API_KEY; ~1 API call per case)

Each case gets a fresh cache, so the real API is exercised every time.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from dotenv import load_dotenv
from PIL import Image

from google.genai.errors import APIError

from fakturama_agent.extract.llm import RETRYABLE_CODES, ExtractionError, GeminiConfig, GeminiExtractor
from fakturama_agent.extract.pipeline import run_extract

SAMPLE = Path(__file__).parents[1] / "samples" / "order_001.png"

pytestmark = pytest.mark.live


def _save(image: Image.Image, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def original(img: Image.Image) -> tuple[bytes, str]:
    return SAMPLE.read_bytes(), ".png"


def exif_rotated(img: Image.Image) -> tuple[bytes, str]:
    """Like a phone photo: pixels stored sideways, EXIF orientation 6 says 'rotate to view'."""
    exif = Image.Exif()
    exif[0x0112] = 6
    return _save(img.convert("RGB").transpose(Image.Transpose.ROTATE_90), "JPEG", quality=92, exif=exif), ".jpg"


def downscaled_half(img: Image.Image) -> tuple[bytes, str]:
    return _save(img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS), "PNG"), ".png"


def jpeg_q60(img: Image.Image) -> tuple[bytes, str]:
    return _save(img.convert("RGB"), "JPEG", quality=60), ".jpg"


def skewed_1deg(img: Image.Image) -> tuple[bytes, str]:
    """Like a slightly crooked scan."""
    return _save(img.convert("RGB").rotate(1, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white"), "PNG"), ".png"


def sideways_no_exif(img: Image.Image) -> tuple[bytes, str]:
    """Rotated 90 degrees with no EXIF hint: nothing tells us how to turn it back."""
    return _save(img.transpose(Image.Transpose.ROTATE_90), "PNG"), ".png"


# Variants the pipeline must read correctly.
MUST_PASS: list[Callable] = [original, exif_rotated, downscaled_half, jpeg_q60, skewed_1deg]
# Variants it may reject, but must never turn into a wrong spec.
MAY_REVIEW: list[Callable] = [sideways_no_exif]


@pytest.fixture(scope="module")
def sample_image() -> Image.Image:
    return Image.open(SAMPLE)


def _extract(variant: Callable, sample_image: Image.Image, tmp_path: Path):
    load_dotenv(Path(".env"))
    data, suffix = variant(sample_image)
    path = tmp_path / f"{variant.__name__}{suffix}"
    path.write_bytes(data)
    extractor = GeminiExtractor(config=GeminiConfig.from_env(cache_dir=tmp_path / "cache"))
    try:
        return run_extract(path, extractor)
    except ExtractionError as e:
        # Gemini overloaded/down after all retries says nothing about our code; anything else fails.
        if isinstance(e.__cause__, APIError) and e.__cause__.code in RETRYABLE_CODES:
            pytest.skip(f"Gemini unavailable: {e}")
        raise


def _report(outcome) -> str:
    return "\n".join(str(i) for i in [*outcome.issues, *outcome.warnings]) or "no issues"


@pytest.mark.parametrize("variant", MUST_PASS, ids=lambda v: v.__name__)
def test_variant_matches_golden(variant, sample_image, tmp_path, golden_spec):
    outcome = _extract(variant, sample_image, tmp_path)
    assert outcome.ok, _report(outcome)
    assert outcome.spec.model_copy(update={"provenance": golden_spec.provenance}) == golden_spec


@pytest.mark.parametrize("variant", MAY_REVIEW, ids=lambda v: v.__name__)
def test_variant_is_never_wrong(variant, sample_image, tmp_path, golden_spec):
    outcome = _extract(variant, sample_image, tmp_path)
    if outcome.spec is not None:
        assert outcome.spec.model_copy(update={"provenance": golden_spec.provenance}) == golden_spec
