"""Cross-check LLM values against the OCR text.

Every value the LLM copied is looked up in the OCR boxes:

- confirmed:   the same characters appear in one OCR box;
- conflict:    only a look-alike variant appears (0/O, 1/l/I, 5/S, 8/B, 2/Z, 6/G,
               or a case difference), so LLM and OCR disagree;
- unconfirmed: nothing comparable appears.

Conflicts and unconfirmed values stop the run (Issue) for critical fields and
are warnings for free text.

Matching ignores whitespace (OCR often drops spaces: ``NorthstarOfficeGmbH``),
stays within one box (no layout logic) and respects token boundaries by
character class, so ``2`` is not confirmed by ``2026`` while ``10117`` is
confirmed by ``10117Berlin``.

Arithmetic cannot catch a wrong identifier (CHR-ERG-O1 sums the same as
CHR-ERG-01); this check can.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from fakturama_agent.extract.ocr import OcrBox, OcrResult
from fakturama_agent.spec.models import CheckStatus, FieldCheck, Issue, RawExtraction

# Characters OCR and vision models confuse. Applied after upper-casing, so a
# case-only difference also folds together (and is reported as a conflict).
_CONFUSABLES = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "I": "1",
        "L": "1",
        "|": "1",
        "!": "1",
        "S": "5",
        "B": "8",
        "Z": "2",
        "G": "6",
        "–": "-",  # en dash
        "—": "-",  # em dash
        "−": "-",  # minus sign
    }
)

# Look-alike search on 1-2 characters matches noise ('1' vs a '|' separator);
# such values must match exactly or count as unconfirmed.
MIN_CONFUSABLE_LEN = 3

# Free text: a mismatch is worth a warning, not a stop. Everything else is used
# as an exact key (Debtor/Product lookup) or changes the invoice.
_NON_CRITICAL_SUFFIXES = (".description", "_address.name", "_address.country", ".unit")


def _fold(text: str) -> str:
    return text.upper().translate(_CONFUSABLES)


def _strip(text: str) -> str:
    return "".join(text.split())


def _char_class(ch: str) -> str:
    if ch.isdigit():
        return "digit"
    if ch.isalpha():
        return "alpha"
    return "other"


def _bounded(haystack: str, start: int, end: int, needle: str) -> bool:
    """Token boundary: no same-class character glued to either edge."""
    first, last = _char_class(needle[0]), _char_class(needle[-1])
    if start > 0 and first != "other" and _char_class(haystack[start - 1]) == first:
        return False
    if end < len(haystack) and last != "other" and _char_class(haystack[end]) == last:
        return False
    return True


def _find(text: str, token: str, *, folded: bool) -> int | None:
    """First bounded occurrence of ``token`` in ``text`` (optionally both confusable-folded).

    Folding maps one character to one character, so positions line up and the
    boundary is judged on the original characters.
    """
    haystack, needle = (_fold(text), _fold(token)) if folded else (text, token)
    start = haystack.find(needle)
    while start != -1:
        end = start + len(token)
        # The OCR side must also be a whole token: 'L' inside 'Labs' is no look-alike of '1'.
        if _bounded(text, start, end, token) and _bounded(text, start, end, text[start:end]):
            return start
        start = haystack.find(needle, start + 1)
    return None


def check_value(value: str, boxes: tuple[OcrBox, ...]) -> tuple[CheckStatus, str | None]:
    token = _strip(value)
    if not token:
        return CheckStatus.CONFIRMED, None
    texts = [_strip(b.text) for b in boxes]
    if any(_find(t, token, folded=False) is not None for t in texts):
        return CheckStatus.CONFIRMED, None
    if len(token) < MIN_CONFUSABLE_LEN:
        return CheckStatus.UNCONFIRMED, None
    for t in texts:
        pos = _find(t, token, folded=True)
        if pos is not None:
            return CheckStatus.CONFLICT, t[pos : pos + len(token)]
    return CheckStatus.UNCONFIRMED, None


def _values(raw: RawExtraction) -> Iterator[tuple[str, str | None]]:
    simple = {
        "order.external_reference": raw.external_reference,
        "order.order_date": raw.order_date,
        "order.customer_id": raw.customer_id,
        "order.currency": raw.currency,
        "order.status_badge": raw.status_badge,
        "debtor.company": raw.company,
        "debtor.alias": raw.customer_alias,
        "debtor.contact_name": raw.contact_name,
        "debtor.email": raw.email,
        "debtor.phone": raw.phone,
        "payment.method": raw.payment_method,
        "payment.paid_status": raw.paid_status,
        "payment.payment_date": raw.payment_date,
        "totals.net": raw.net_total,
        "totals.vat": raw.vat_total,
        "totals.gross": raw.gross_total,
    }
    yield from simple.items()
    for prefix in ("billing", "delivery"):
        for name in ("name", "street", "zip", "city", "country"):
            yield f"{prefix}_address.{name}", getattr(raw, f"{prefix}_{name}")
    for i, item in enumerate(raw.items):
        # line_no is not checked: Fakturama numbers lines itself, and OCR often
        # misses a lone digit in the margin.
        for name in ("sku", "description", "quantity", "unit", "unit_net_price", "discount", "vat_rate", "line_net"):
            yield f"items[{i}].{name}", getattr(item, name)


def is_critical(field_name: str) -> bool:
    return not field_name.endswith(_NON_CRITICAL_SUFFIXES)


@dataclass
class CrossCheckResult:
    checks: list[FieldCheck] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)


def crosscheck(raw: RawExtraction, ocr: OcrResult) -> CrossCheckResult:
    result = CrossCheckResult()
    for name, value in _values(raw):
        if value is None or not value.strip():
            continue  # absence is the normalizer's business
        status, ocr_text = check_value(value, ocr.boxes)
        critical = is_critical(name)
        result.checks.append(FieldCheck(field=name, value=value, status=status, critical=critical, ocr_text=ocr_text))
        if status is CheckStatus.CONFIRMED:
            continue
        rule = "ocr_conflict" if status is CheckStatus.CONFLICT else "ocr_unconfirmed"
        issue = Issue(rule=rule, field=name, expected=value, observed=ocr_text)
        (result.issues if critical else result.warnings).append(issue)
    return result
