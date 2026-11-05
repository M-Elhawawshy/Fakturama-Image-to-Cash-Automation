"""Part F: is the VAT rate / term of payment the Order needs already defined correctly?

Brief 2.10.2 / 3.5 (PHASE3_PLAN decision 4): a record is reused only if its
definition matches exactly; an existing record with the same name but another
value or code is a conflicting definition -> manual review; the tool creates
missing records (part G) but never edits existing ones.

VAT: the row named like ``derived.items[].vat_name`` (e.g. ``VAT 19%``) must have
Value = rate and VAT code (E-Invoice) ``S`` (standard rate; other rates are not
supported and go to review). Payment: the row named like the method (e.g. ``Bank
Transfer``) must have the payment code of the brief's mapping (e.g. ``Credit
transfer``; Fakturama shows it with a trailing space). Read only: each editor is
opened from its Data view row and closed unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from fakturama_agent.spec.derive import payment_code, vat_name
from fakturama_agent.spec.models import PaymentMethod
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.record_editor import EditorError, RecordEditor, read_view

Kind = Literal["reusable", "missing", "conflict", "ambiguous", "unsupported"]
STANDARD_CODE = "S"


@dataclass
class MasterCheck:
    what: str  # "VAT 19%" / "Payment Bank Transfer"
    kind: Kind | None = None
    reason: str = ""
    seen: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Decided without review: reuse it, or create it in part G."""
        return self.error is None and self.kind in ("reusable", "missing")

    def summary(self) -> str:
        if self.error:
            return f"STOPPED: {self.error}"
        text = {"reusable": "exists and matches (reuse)", "missing": "missing (create in part G)",
                "conflict": "CONFLICTING definition, review", "ambiguous": "AMBIGUOUS, review",
                "unsupported": "UNSUPPORTED, review"}[self.kind]
        seen = f" [{', '.join(f'{k}={v!r}' for k, v in self.seen.items())}]" if self.seen else ""
        return f"{text}: {self.reason}{seen}"


def _decimal(text: str | None) -> Decimal | None:
    try:
        return Decimal((text or "").strip().rstrip("%").strip())
    except InvalidOperation:
        return None


# --- pure rules ------------------------------------------------------------------------


def rows_named(rows: list[dict], name: str) -> list[int]:
    return [i for i, r in enumerate(rows) if (r.get("Name") or "") == name]


def judge_vat(name: str, rate: Decimal, table_value: str | None, shown: dict[str, str]) -> tuple[Kind, str]:
    """``table_value`` is the copied model value (0.19); ``shown`` the editor fields."""
    problems = []
    if shown.get("Name") != name:
        problems.append(f"editor Name is {shown.get('Name')!r}")
    if _decimal(table_value) != rate / 100:
        problems.append(f"table Value is {table_value!r}, expected {rate / 100}")
    if _decimal(shown.get("Value")) != rate:
        problems.append(f"Value shows {shown.get('Value')!r}, expected {rate}%")
    code = (shown.get("VAT code") or "").split(" ", 1)[0]
    if code != STANDARD_CODE:
        problems.append(f"VAT code is {shown.get('VAT code')!r}, expected {STANDARD_CODE} (Standard rate)")
    if problems:
        return "conflict", "; ".join(problems)
    return "reusable", f"{name} = {rate}%, code {STANDARD_CODE}"


def judge_payment(name: str, code: str, shown: dict[str, str]) -> tuple[Kind, str]:
    problems = []
    if shown.get("Name") != name:
        problems.append(f"editor Name is {shown.get('Name')!r}")
    stored = (shown.get("Payment code") or "").strip()
    if stored != code:
        problems.append(f"payment code is {stored or 'empty'!r}, the brief maps {name} to {code!r}")
    if problems:
        return "conflict", "; ".join(problems)
    return "reusable", f"{name} has code {code!r}"


# --- live checks ---------------------------------------------------------------------------


def _check(main: MainWindow, registry: Registry, ocr: Callable, out: MasterCheck, table_key: str, name: str,
           read_editor: Callable[[RecordEditor], dict[str, str]], judge: Callable[[dict, dict], tuple[Kind, str]],
           focus_key: str) -> MasterCheck:
    editor = None
    try:
        grid, table = read_view(main, registry, table_key, ocr)
        matches = rows_named(table.rows, name)
        if not matches:
            out.kind, out.reason = "missing", f"no {table.view} row named {name!r} ({len(table.rows)} rows)"
            return out
        if len(matches) > 1:
            out.kind, out.reason = "ambiguous", f"{len(matches)} rows named {name!r}"
            return out
        row = table.rows[matches[0]]
        editor = RecordEditor.open_row(main, registry, grid, table, matches[0])
        out.seen = read_editor(editor)
        out.kind, out.reason = judge(row, out.seen)
    except (EditorError, TimeoutError) as e:
        out.error = str(e)
    except Exception as e:  # noqa: BLE001 - reported as a stop, never retried
        out.error = f"{type(e).__name__}: {e}"
    finally:
        if editor is not None:
            try:
                editor.close_unchanged(focus_key)
            except Exception as e:  # noqa: BLE001
                out.error = (out.error + "; " if out.error else "") + str(e)
    return out


def check_vat(main: MainWindow, registry: Registry, rate: Decimal, ocr: Callable) -> MasterCheck:
    name = vat_name(rate)
    out = MasterCheck(name)
    if rate <= 0:
        out.kind, out.reason = "unsupported", "only standard rates (code S) are handled; 0% needs an exemption code"
        return out

    def read_editor(ed: RecordEditor) -> dict[str, str]:
        return {"Name": ed.value("vat.name"), "Value": ed.value("vat.value"), "VAT code": ed.value("vat.code")}

    return _check(main, registry, ocr, out, "vats", name, read_editor,
                  lambda row, shown: judge_vat(name, rate, row.get("Value"), shown), "vat.name")


def check_payment(main: MainWindow, registry: Registry, method: PaymentMethod, ocr: Callable) -> MasterCheck:
    name, code = method.value, payment_code(method)
    out = MasterCheck(f"Payment {name}")

    def read_editor(ed: RecordEditor) -> dict[str, str]:
        return {"Name": ed.value("payment.name"), "Payment code": ed.value("payment.code")}

    return _check(main, registry, ocr, out, "payments", name, read_editor,
                  lambda row, shown: judge_payment(name, code, shown), "payment.name")
