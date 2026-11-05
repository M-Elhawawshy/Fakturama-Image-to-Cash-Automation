"""Human-readable output: review.md for manual review, a short summary on success."""

from __future__ import annotations

from fakturama_agent.extract.pipeline import ExtractOutcome
from fakturama_agent.spec.models import CheckStatus, Issue

_STATUS_MARK = {CheckStatus.CONFIRMED: "ok", CheckStatus.CONFLICT: "CONFLICT", CheckStatus.UNCONFIRMED: "not found"}


def _cell(value: object) -> str:
    if value is None:
        return "—"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return f"`{text}`" if text else "—"


def _issue_table(issues: list[Issue]) -> list[str]:
    lines = ["| Rule | Field | Expected | Observed |", "|---|---|---|---|"]
    lines += [f"| {i.rule} | {i.field} | {_cell(i.expected)} | {_cell(i.observed)} |" for i in issues]
    return lines


def _header(outcome: ExtractOutcome) -> list[str]:
    img = outcome.image
    prep = "; ".join(img.steps) if img.steps else "none (used as is)"
    return [
        f"- Image: `{outcome.image_path}` ({img.size[0]}x{img.size[1]})",
        f"- SHA-256: `{img.source_sha256}`",
        f"- Preprocessing: {prep}",
        f"- LLM: `{outcome.model_id}`{' (cached response)' if outcome.from_cache else ''}",
        f"- OCR: `{outcome.ocr_engine}`",
    ]


def render_review(outcome: ExtractOutcome, raw_path_name: str = "raw.json") -> str:
    lines = [
        "# Manual review required",
        "",
        f"The order was **not** written: {len(outcome.issues)} issue(s), {len(outcome.warnings)} warning(s).",
        "",
        *_header(outcome),
        "",
        "## Issues",
        "",
        *_issue_table(outcome.issues),
        "",
    ]
    if outcome.warnings:
        lines += ["## Warnings", "", *_issue_table(outcome.warnings), ""]

    lines += [
        "## What the LLM read, and what OCR saw",
        "",
        "| Field | Value read | OCR | OCR text |",
        "|---|---|---|---|",
    ]
    for c in outcome.checks:
        mark = _STATUS_MARK[c.status] + ("" if c.critical else " (free text)")
        lines.append(f"| {c.field} | {_cell(c.value)} | {mark} | {_cell(c.ocr_text)} |")

    lines += [
        "",
        "## Rules",
        "",
        "- `ocr_conflict`: OCR shows a look-alike of the value (0/O, 1/l, 5/S, ...). Check the image.",
        "- `ocr_unconfirmed`: OCR could not find the value at all.",
        "- `parse_*` / `map_*` / `split_*` / `required`: the value is missing or not in an accepted format.",
        "- `line_net`, `net_total`, `vat_total`, `gross_total`: the printed numbers do not add up.",
        "",
        "## Next steps",
        "",
        f"1. Compare the flagged fields with the image. The LLM's full reading is in `{raw_path_name}`.",
        "2. If the image is at fault, fix or rescan it and run `extract` again.",
        f"3. If the reading only needs a correction, edit `{raw_path_name}` and run",
        f"   `python -m fakturama_agent normalize {raw_path_name} -o order.json`",
        "   (this re-runs normalization and the arithmetic checks, not OCR).",
        "",
    ]
    return "\n".join(lines)


def render_summary(outcome: ExtractOutcome) -> str:
    spec = outcome.spec
    assert spec is not None
    confirmed = sum(c.status is CheckStatus.CONFIRMED for c in outcome.checks)
    lines = [
        f"Order {spec.order.external_reference} of {spec.order.order_date} for {spec.debtor.company}",
        f"  {len(spec.items)} item(s), net {spec.totals.net} + VAT {spec.totals.vat} = gross {spec.totals.gross} "
        f"{spec.order.currency}",
        f"  payment: {spec.payment.method} ({spec.derived.payment_code}), {spec.payment.paid_status}"
        + (f" on {spec.payment.payment_date}" if spec.payment.payment_date else ""),
        f"  OCR confirmed {confirmed}/{len(outcome.checks)} values; {len(outcome.warnings)} warning(s)",
    ]
    lines += [f"  warning: {w}" for w in outcome.warnings]
    return "\n".join(lines)
