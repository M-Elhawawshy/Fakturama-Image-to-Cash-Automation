"""Command-line entry point.

Exit codes: 0 = OrderSpec written, 1 = operational error (no API key, API
down, unreadable file; just retry), 2 = manual review needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fakturama_agent.extract.llm import ExtractionError, GeminiConfig, GeminiExtractor, VisionExtractor
from fakturama_agent.extract.normalize import normalize
from fakturama_agent.extract.ocr import run_ocr
from fakturama_agent.extract.pipeline import OcrFn, run_extract
from fakturama_agent.extract.report import render_review, render_summary
from fakturama_agent.extract.validate import validate
from fakturama_agent.spec.models import Issue, OrderSpec, RawExtraction

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REVIEW = 2

REVIEW_NAME = "review.md"
RAW_NAME = "raw.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fakturama_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    ext = sub.add_parser("extract", help="order image -> validated OrderSpec JSON (or review.md)")
    ext.add_argument("image", type=Path, help="order image (PNG, JPEG, WebP; other formats are converted)")
    ext.add_argument("-o", "--output", type=Path, required=True, help="OrderSpec JSON to write")
    ext.add_argument("--no-cache", action="store_true", help="ignore the cached LLM response and call the API")

    norm = sub.add_parser("normalize", help="RawExtraction JSON (e.g. a corrected raw.json) -> OrderSpec JSON")
    norm.add_argument("raw", type=Path, help="RawExtraction JSON file")
    norm.add_argument("-o", "--output", type=Path, required=True, help="OrderSpec JSON to write")
    return parser


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_spec(spec: OrderSpec, out_path: Path) -> None:
    _write_text(out_path, spec.model_dump_json(indent=2) + "\n")


def _report_stderr(issues: list[Issue]) -> int:
    print(f"Manual review required ({len(issues)} issue(s)):", file=sys.stderr)
    for issue in issues:
        print(f"  - {issue}", file=sys.stderr)
    return EXIT_REVIEW


def cmd_extract(image: Path, out_path: Path, extractor: VisionExtractor, ocr: OcrFn = run_ocr) -> int:
    review_path = out_path.parent / REVIEW_NAME
    raw_path = out_path.parent / RAW_NAME
    # A spec or review left over from an earlier run must never be mistaken for this run's result.
    for stale in (out_path, review_path, raw_path):
        stale.unlink(missing_ok=True)

    try:
        outcome = run_extract(image, extractor, ocr)
    except ExtractionError as e:
        print(f"Extraction failed (nothing written, safe to retry): {e}", file=sys.stderr)
        return EXIT_ERROR

    _write_text(raw_path, outcome.raw.model_dump_json(indent=2) + "\n")
    if not outcome.ok:
        _write_text(review_path, render_review(outcome, RAW_NAME))
        _report_stderr(outcome.issues)
        print(f"See {review_path}", file=sys.stderr)
        return EXIT_REVIEW

    _write_spec(outcome.spec, out_path)
    print(render_summary(outcome))
    print(f"Wrote {out_path}")
    return EXIT_OK


def cmd_normalize(raw_path: Path, out_path: Path) -> int:
    raw = RawExtraction.model_validate_json(raw_path.read_text(encoding="utf-8"))
    result = normalize(raw)
    if result.spec is None:
        return _report_stderr(result.issues)
    issues = validate(result.spec)
    if issues:
        return _report_stderr(issues)
    _write_spec(result.spec, out_path)
    print(f"Wrote {out_path}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "extract":
        from dotenv import load_dotenv

        load_dotenv(Path(".env"))
        overrides = {"cache_dir": None} if args.no_cache else {}
        extractor = GeminiExtractor(config=GeminiConfig.from_env(**overrides))
        return cmd_extract(args.image, args.output, extractor)
    if args.command == "normalize":
        return cmd_normalize(args.raw, args.output)
    return EXIT_REVIEW  # pragma: no cover - argparse enforces a known command
