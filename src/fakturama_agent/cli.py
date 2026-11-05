"""Command-line entry point.

Exit codes: 0 = OrderSpec written, 1 = operational error (no API key, API
down, unreadable file; just retry), 2 = manual review needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fakturama_agent.extract.crosscheck import crosscheck
from fakturama_agent.extract.llm import ExtractionError, GeminiConfig, GeminiExtractor, VisionExtractor
from fakturama_agent.extract.normalize import normalize
from fakturama_agent.extract.ocr import run_ocr
from fakturama_agent.extract.ocr_report import render_boxes, render_check, save_overlay
from fakturama_agent.extract.pipeline import OcrFn, run_extract
from fakturama_agent.extract.preprocess import prepare_image
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

    ocr = sub.add_parser("ocr", help="run only the OCR on an image (no LLM) and show what it read")
    ocr.add_argument("image", type=Path, help="order image")
    ocr.add_argument("--check", type=Path, metavar="RAW_JSON", help="cross-check a RawExtraction (e.g. out/raw.json)")
    ocr.add_argument("--overlay", type=Path, metavar="PNG", help="save the image with numbered OCR boxes drawn on it")
    ocr.add_argument("--json", type=Path, metavar="JSON", help="save the raw OCR result as JSON")

    ins = sub.add_parser("inspect", help="dump Fakturama's UIA tree (read-only)")
    ins.add_argument("-o", "--output", type=Path, help="write the snapshot as JSON")
    ins.add_argument("--window", metavar="TITLE_REGEX", help="a Fakturama dialog/window instead of the main window")
    ins.add_argument("--max-depth", type=int, default=40)
    ins.add_argument("--max-nodes", type=int, default=6000)
    ins.add_argument("--quiet", action="store_true", help="do not print the outline")

    loc = sub.add_parser("locate", help="resolve locators from locators.yaml in the live Fakturama (read-only)")
    loc.add_argument("keys", nargs="*", help="locator keys (default: all verified ones)")
    loc.add_argument("--all", action="store_true", help="include unverified locators")

    pre = sub.add_parser(
        "preflight", help="check Fakturama is ready to automate (opens Preferences, closes it with Cancel)"
    )
    pre.add_argument("--json", type=Path, dest="json_out", help="also write the report as JSON")

    tab = sub.add_parser(
        "read-table",
        help="read a Data view by Select All + Copy (overwrites the clipboard; changes nothing in Fakturama)",
    )
    tab.add_argument(
        "table", choices=["documents", "debtors", "creditors", "products", "vats", "shippings", "payments"]
    )
    tab.add_argument("--json", type=Path, dest="json_out", help="also write the rows as JSON")

    ordr = sub.add_parser(
        "order-open", help="open a New Order and set Date, Cust.Ref., Net, With VAT from an OrderSpec (never saves)"
    )
    ordr.add_argument("spec", type=Path, help="OrderSpec JSON (from `extract`)")

    osel = sub.add_parser(
        "order-select",
        help="open a New Order and check the Debtor and each SKU with the selector dialogs (never saves)",
    )
    osel.add_argument("spec", type=Path, help="OrderSpec JSON")

    cv = sub.add_parser("check-vat", help="is 'VAT <rate>%%' defined with that value and code S? (read-only)")
    cv.add_argument("rate", help="VAT percent, e.g. 19")
    cp = sub.add_parser("check-payment", help="is the term of payment defined with the brief's code? (read-only)")
    cp.add_argument("method", help="Bank Transfer | Credit Card | SEPA Direct Debit")
    cm = sub.add_parser("check-master", help="check-vat for every rate in an OrderSpec plus its payment (read-only)")
    cm.add_argument("spec", type=Path, help="OrderSpec JSON")

    warn = " PERSISTS a record: back up the workspace first."
    crp = sub.add_parser("create-payment", help="create a missing term of payment with the brief's code." + warn)
    crp.add_argument("method", help="Bank Transfer | Credit Card | SEPA Direct Debit")
    crv = sub.add_parser("create-vat", help="create a missing 'VAT <rate>%%' with code S." + warn)
    crv.add_argument("rate", help="VAT percent, e.g. 7")
    crpr = sub.add_parser("create-product", help="create missing products of an OrderSpec." + warn)
    crpr.add_argument("spec", type=Path, help="OrderSpec JSON")
    crpr.add_argument("--sku", help="only this item")
    crpr.add_argument("--reselect", action="store_true",
                      help="afterwards select each product from a New Order as proof (not saved)")
    email_help = ("if Fakturama rejects the e-mail (e.g. the .test domain), save the Debtor with an EMPTY "
                  "E-Mail instead of stopping for review")
    crd = sub.add_parser("create-debtor", help="create the missing Debtor of an OrderSpec (both addresses, "
                         "payment)." + warn)
    crd.add_argument("spec", type=Path, help="OrderSpec JSON")
    crd.add_argument("--without-email", action="store_true", help=email_help)
    crd.add_argument("--reselect", action="store_true",
                     help="afterwards select the Debtor from a New Order and check both addresses (not saved)")
    crm = sub.add_parser("create-master", help="create the payment term, VAT rates, products and Debtor an "
                         "OrderSpec needs, in that order." + warn)
    crm.add_argument("spec", type=Path, help="OrderSpec JSON")
    crm.add_argument("--without-email", action="store_true", help=email_help)

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


def cmd_ocr(
    image: Path,
    check: Path | None = None,
    overlay: Path | None = None,
    json_out: Path | None = None,
    ocr: OcrFn = run_ocr,
) -> int:
    """OCR only. Exit 0, or 2 when --check finds issues that would stop `extract`."""
    try:
        prepared = prepare_image(image)
    except ExtractionError as e:
        print(f"OCR failed: {e}", file=sys.stderr)
        return EXIT_ERROR
    result = ocr(prepared.data)

    prep = "; ".join(prepared.steps) if prepared.steps else "none"
    print(f"OCR {result.engine} on {image} ({prepared.size[0]}x{prepared.size[1]}, preprocessing: {prep})")
    print(render_boxes(result))

    if json_out:
        _write_text(json_out, result.to_json())
        print(f"\nWrote {json_out}")
    if overlay:
        save_overlay(prepared.data, result, overlay)
        print(f"\nWrote {overlay} (box numbers match the list; orange = low confidence)")
    if check is None:
        return EXIT_OK

    raw = RawExtraction.model_validate_json(check.read_text(encoding="utf-8"))
    checked = crosscheck(raw, result)
    print(f"\nCross-check of {check}:")
    print(render_check(checked))
    return EXIT_REVIEW if checked.issues else EXIT_OK


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
    if args.command == "ocr":
        return cmd_ocr(args.image, args.check, args.overlay, args.json)
    if args.command in ("inspect", "locate", "preflight", "read-table", "order-open", "order-select",
                        "check-vat", "check-payment", "check-master",
                        "create-payment", "create-vat", "create-product", "create-debtor", "create-master"):
        from fakturama_agent.uia import commands

        if args.command == "inspect":
            return commands.cmd_inspect(
                args.output, window_regex=args.window, max_depth=args.max_depth,
                max_nodes=args.max_nodes, show_outline=not args.quiet,
            )
        if args.command == "create-payment":
            return commands.cmd_create("payment", method=args.method)
        if args.command == "create-vat":
            return commands.cmd_create("vat", rate=args.rate)
        if args.command == "create-product":
            return commands.cmd_create("product", spec_path=args.spec, sku=args.sku, reselect=args.reselect)
        if args.command == "create-debtor":
            return commands.cmd_create("debtor", spec_path=args.spec, without_email=args.without_email,
                                       reselect=args.reselect)
        if args.command == "create-master":
            return commands.cmd_create("master", spec_path=args.spec, without_email=args.without_email)
        if args.command == "check-vat":
            return commands.cmd_check_master(rates=[args.rate])
        if args.command == "check-payment":
            return commands.cmd_check_master(method=args.method)
        if args.command == "check-master":
            return commands.cmd_check_master(spec_path=args.spec)
        if args.command == "order-select":
            return commands.cmd_order_select(args.spec)
        if args.command == "order-open":
            return commands.cmd_order_open(args.spec)
        if args.command == "read-table":
            return commands.cmd_read_table(args.table, args.json_out)
        if args.command == "preflight":
            return commands.cmd_preflight(args.json_out)
        return commands.cmd_locate(args.keys, include_unverified=args.all)
    if args.command == "normalize":
        return cmd_normalize(args.raw, args.output)
    return EXIT_REVIEW  # pragma: no cover - argparse enforces a known command
