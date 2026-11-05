"""Prepare the input image for the LLM and OCR passes.

- record the SHA-256 of the file exactly as given (provenance);
- apply the EXIF orientation (phone photos are often stored sideways);
- upscale small images so small print stays legible for OCR;
- anything Pillow can read but Gemini may not (TIFF, BMP, ...) becomes PNG.

An image that needs none of this is passed through byte for byte.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from fakturama_agent.extract.llm import MIME_TYPES, ExtractionError, sha256_hex

MIN_LONG_SIDE = 1600  # px; the sample is 1489 x 2048
MAX_UPSCALE = 4.0
_EXIF_ORIENTATION = 0x0112


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    mime_type: str
    source_sha256: str  # hash of the original file, for provenance
    size: tuple[int, int]  # (width, height) after preparation
    steps: tuple[str, ...]  # what was done, for the report


def prepare_image(path: Path) -> PreparedImage:
    try:
        original = path.read_bytes()
    except OSError as e:
        raise ExtractionError(f"cannot read image {path}: {e}") from e
    return prepare_bytes(original, path.suffix)


def prepare_bytes(original: bytes, suffix: str = "") -> PreparedImage:
    digest = sha256_hex(original)
    try:
        image = Image.open(io.BytesIO(original))
        image.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ExtractionError(f"not a readable image: {e}") from e
    source_format = image.format

    steps: list[str] = []
    orientation = image.getexif().get(_EXIF_ORIENTATION, 1)
    if orientation != 1:
        image = ImageOps.exif_transpose(image)
        steps.append(f"applied EXIF orientation {orientation}")

    long_side = max(image.size)
    if long_side < MIN_LONG_SIDE:
        factor = min(MIN_LONG_SIDE / long_side, MAX_UPSCALE)
        new_size = (round(image.width * factor), round(image.height * factor))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        steps.append(f"upscaled x{factor:.2f} to {new_size[0]}x{new_size[1]}")

    mime = MIME_TYPES.get(suffix.lower())
    if not steps and mime is not None:
        return PreparedImage(original, mime, digest, image.size, ())

    if mime is None:
        steps.append(f"converted {source_format or suffix or 'image'} to PNG")
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return PreparedImage(buffer.getvalue(), "image/png", digest, image.size, tuple(steps))
