"""Re-record the real Gemini and OCR outputs that the offline tests replay.

Run after changing the prompt, the schema or the model:

    python -m uv run python scripts/record_fixtures.py [image] [--name order_001]

Writes tests/fixtures/gemini_<name>.json (same shape as a cache entry) and
tests/fixtures/ocr_<name>.json. Uses the model from GEMINI_MODEL / .env.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from fakturama_agent.extract.llm import GeminiConfig, GeminiExtractor
from fakturama_agent.extract.ocr import run_ocr
from fakturama_agent.extract.preprocess import prepare_image

FIXTURES = Path(__file__).parents[1] / "tests" / "fixtures"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, nargs="?", default=Path("samples/order_001.png"))
    parser.add_argument("--name", default=None, help="fixture suffix (default: image stem)")
    args = parser.parse_args()
    name = args.name or args.image.stem

    load_dotenv(Path(".env"))
    image = prepare_image(args.image)

    with tempfile.TemporaryDirectory() as tmp:
        extractor = GeminiExtractor(config=GeminiConfig.from_env(cache_dir=Path(tmp)))
        result = extractor.extract(image.data, image.mime_type)
        entry = json.loads(extractor.cache_path(result.image_sha256).read_text(encoding="utf-8"))

    gemini_path = FIXTURES / f"gemini_{name}.json"
    gemini_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {gemini_path} ({entry['model_id']}, prompt {entry['prompt_version']})")

    ocr_path = FIXTURES / f"ocr_{name}.json"
    ocr_path.write_text(run_ocr(image.data).to_json(), encoding="utf-8", newline="\n")
    print(f"Wrote {ocr_path}")


if __name__ == "__main__":
    main()
