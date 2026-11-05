import io
from pathlib import Path

import pytest
from PIL import Image

from fakturama_agent.extract.llm import ExtractionError, sha256_hex
from fakturama_agent.extract.preprocess import MIN_LONG_SIDE, prepare_bytes, prepare_image

SAMPLE = Path(__file__).parents[1] / "samples" / "order_001.png"


def _encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def _decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_sample_passes_through_unchanged():
    prepared = prepare_image(SAMPLE)
    original = SAMPLE.read_bytes()
    assert prepared.data == original
    assert prepared.mime_type == "image/png"
    assert prepared.source_sha256 == sha256_hex(original)
    assert prepared.size == (1489, 2048)
    assert prepared.steps == ()


def test_exif_rotation_is_applied():
    # A 300x200 landscape image stored with "rotate 90" (orientation 6) displays as portrait.
    image = Image.new("RGB", (2000, 1000), "white")
    exif = Image.Exif()
    exif[0x0112] = 6
    data = _encode(image, "JPEG", exif=exif)
    prepared = prepare_bytes(data, ".jpg")
    assert prepared.size == (1000, 2000)
    assert _decode(prepared.data).size == (1000, 2000)
    assert prepared.mime_type == "image/png"
    assert prepared.source_sha256 == sha256_hex(data)
    assert prepared.steps == ("applied EXIF orientation 6",)


def test_small_image_is_upscaled():
    data = _encode(Image.new("RGB", (400, 600), "white"), "PNG")
    prepared = prepare_bytes(data, ".png")
    assert max(prepared.size) == MIN_LONG_SIDE
    assert prepared.size == (1067, 1600)
    assert prepared.steps[0].startswith("upscaled x2.67")


def test_upscale_is_capped():
    prepared = prepare_bytes(_encode(Image.new("L", (100, 50)), "PNG"), ".png")
    assert prepared.size == (400, 200)  # x4 cap


def test_other_formats_become_png():
    data = _encode(Image.new("CMYK", (2000, 1600)), "TIFF")
    prepared = prepare_bytes(data, ".tif")
    assert prepared.mime_type == "image/png"
    assert _decode(prepared.data).format == "PNG"
    assert prepared.steps == ("converted TIFF to PNG",)


def test_unreadable_file_is_an_error(tmp_path):
    bad = tmp_path / "order.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ExtractionError, match="not a readable image"):
        prepare_image(bad)
    with pytest.raises(ExtractionError, match="cannot read"):
        prepare_image(tmp_path / "missing.png")
