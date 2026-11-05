"""Command-line entry point.

Exit codes: 0 = spec written, 2 = manual review needed (issues on stderr).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fakturama_agent.extract.normalize import normalize
from fakturama_agent.extract.validate import validate
from fakturama_agent.spec.models import Issue, RawExtraction

EXIT_OK = 0
EXIT_REVIEW = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fakturama_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    # Temporary until the LLM extractor exists (step 6 adds `extract`).
    norm = sub.add_parser("normalize", help="RawExtraction JSON -> validated OrderSpec JSON")
    norm.add_argument("raw", type=Path, help="RawExtraction JSON file")
    norm.add_argument("-o", "--output", type=Path, required=True, help="OrderSpec JSON to write")
    return parser


def _report(issues: list[Issue]) -> int:
    print(f"Manual review required ({len(issues)} issue(s)):", file=sys.stderr)
    for issue in issues:
        print(f"  - {issue}", file=sys.stderr)
    return EXIT_REVIEW


def cmd_normalize(raw_path: Path, out_path: Path) -> int:
    raw = RawExtraction.model_validate_json(raw_path.read_text(encoding="utf-8"))
    result = normalize(raw)
    if result.spec is None:
        return _report(result.issues)
    issues = validate(result.spec)
    if issues:
        return _report(issues)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(result.spec.model_dump_json(indent=2) + "\n")
    print(f"Wrote {out_path}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "normalize":
        return cmd_normalize(args.raw, args.output)
    return EXIT_REVIEW  # pragma: no cover - argparse enforces a known command
