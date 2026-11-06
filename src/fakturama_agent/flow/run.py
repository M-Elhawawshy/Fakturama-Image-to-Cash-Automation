"""Part J: ``run`` - one OrderSpec to a saved, verified Order, as explicit steps.

Steps, each with outcome ok (exists / verified), create (a record was saved),
review or stop (both end the run; Fakturama is left exactly as it is):

0. spec gate: every critical extracted field must be OCR-confirmed;
1. journal gate: the previous run for this Cust.Ref. must not have left a Save of
   unknown outcome (``--checked`` after a person looked);
2. preflight, Error.log baseline, duplicate guard (no Order with the Cust.Ref.);
3. master data, check-first and create-if-missing, in the safe order: term of
   payment, VAT rates, products, Debtor (the Debtor editor reads the payment list
   once, a product needs its VAT);
4. the Order: header, selections, lines, totals, addresses, Save once, Documents;
5. after every step: Fakturama's Error.log must not have grown.

Master data is settled before the Order is opened (unlike creating it from inside
the Order): every editor then sees fresh lists, and the Order's selections double
as the proof that the records exist.

Every step goes through a ``Fakturama`` adapter: ``LiveFakturama`` drives the app,
``flow.simulator.FakeFakturama`` simulates it, so the decision tree is tested offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from fakturama_agent.flow.journal import Journal, previous_unresolved
from fakturama_agent.spec.models import CheckStatus, Item, OrderSpec, PaymentMethod

Outcome = Literal["ok", "create", "review", "stop"]


@dataclass
class StepResult:
    """What a Fakturama adapter reports for one step."""

    outcome: Outcome
    detail: str
    saved: bool = False  # a Save was dispatched in this step


class Fakturama(Protocol):
    def preflight(self) -> StepResult: ...
    def error_log(self) -> str: ...  # Fakturama's Error.log content ("" if none)
    def orders_with_ref(self, cust_ref: str) -> list[str] | None: ...  # None: unreadable
    def ensure_payment(self, method: PaymentMethod) -> StepResult: ...
    def ensure_vat(self, rate: Decimal) -> StepResult: ...
    def ensure_product(self, item: Item, gross: Decimal) -> StepResult: ...
    def ensure_debtor(self, spec: OrderSpec, without_email: bool) -> StepResult: ...
    def create_order(self, spec: OrderSpec, allow_duplicate: bool) -> StepResult: ...
    def evidence(self, step: str, moment: str) -> None: ...  # screenshot etc.; may do nothing
    def dump_on_failure(self, step: str) -> None: ...


@dataclass
class RunOptions:
    without_email: bool = False
    allow_duplicate: bool = False
    checked: bool = False  # a person checked Fakturama after an unknown Save of an earlier run


@dataclass
class Record:
    step: str
    outcome: Outcome
    detail: str
    saved: bool = False


@dataclass
class RunResult:
    records: list[Record] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.records) and all(r.outcome in ("ok", "create") for r in self.records)

    @property
    def final(self) -> Record | None:
        return self.records[-1] if self.records else None


SAVING_STEPS = ("payment", "vat", "product", "debtor", "order")


def spec_gate(spec: OrderSpec) -> list[str]:
    """Pure: critical extracted values that OCR did not confirm (manual review)."""
    return [f"{c.field} = {c.value!r} is {c.status.value}" for c in spec.provenance.field_checks
            if c.critical and c.status != CheckStatus.CONFIRMED]


def plan(spec: OrderSpec) -> list[tuple[str, str, object]]:
    """Pure: (kind, label, argument) for every master-data step, in the safe order."""
    steps: list[tuple[str, str, object]] = [("payment", f"Payment {spec.payment.method.value}", spec.payment.method)]
    for rate in sorted({i.vat_percent for i in spec.items}):
        steps.append(("vat", f"VAT {rate.normalize():f}%", rate))
    for item, d in zip(spec.items, spec.derived.items, strict=True):
        steps.append(("product", f"Product {item.sku}", (item, d.product_gross_price)))
    steps.append(("debtor", f"Debtor {spec.debtor.company}", spec))
    return steps


def run_order(spec: OrderSpec, fak: Fakturama, journal: Journal, runs_root: Path,
              options: RunOptions | None = None) -> RunResult:
    options = options or RunOptions()
    result = RunResult()
    ref = spec.order.external_reference

    def record(step: str, outcome: Outcome, detail: str, saved: bool = False) -> bool:
        result.records.append(Record(step, outcome, detail, saved))
        if outcome in ("review", "stop"):
            fak.dump_on_failure(step)
        return outcome in ("ok", "create")

    earlier = previous_unresolved(runs_root, ref)  # before this run's own journal exists
    journal.write("start", "run", cust_ref=ref, options=vars(options))
    problems = spec_gate(spec)
    if not record("spec", "review" if problems else "ok",
                  "; ".join(problems) if problems else "every critical value OCR-confirmed"):
        return _end(journal, result)

    if earlier and not options.checked:
        path, unknown = earlier
        record("journal", "review", f"the run in {path.parent.name} left a Save of unknown outcome: {unknown}. "
                                    "Check Fakturama's Data views, then rerun with --checked")
        return _end(journal, result)
    if earlier:
        journal.write("checked", "journal", earlier=str(earlier[0]), unknown=earlier[1])
    record("journal", "ok", "earlier unknown Save checked by a person (--checked)" if earlier else
           "no earlier run left a Save of unknown outcome")

    pre = fak.preflight()
    if not record("preflight", pre.outcome, pre.detail):
        return _end(journal, result)
    baseline = fak.error_log()

    orders = fak.orders_with_ref(ref)
    if orders is None:
        record("duplicate guard", "stop", "Data > Documents is unreadable")
        return _end(journal, result)
    if orders and not options.allow_duplicate:
        record("duplicate guard", "review", f"Orders {orders} already carry Cust.Ref. {ref!r} "
                                            "(--allow-duplicate creates another)")
        return _end(journal, result)
    record("duplicate guard", "ok", f"no Order carries {ref!r}" if not orders else f"overridden: {orders} exist")

    steps = [*plan(spec), ("order", f"Order {ref}", spec)]
    for kind, label, arg in steps:
        fak.evidence(label, "before")
        journal.write("attempting", label, kind=kind)
        try:
            step = _dispatch(fak, kind, arg, options)
        except Exception as e:  # noqa: BLE001 - outcome unknown: the journal keeps 'attempting' open
            journal.write("failed", label, error=f"{type(e).__name__}: {e}")
            record(label, "stop", f"{type(e).__name__}: {e} (a Save may have happened: check by hand, "
                                  "then rerun with --checked)", saved=True)
            return _end(journal, result)
        journal.write("done", label, outcome=step.outcome, saved=step.saved, detail=step.detail)
        fak.evidence(label, "after")
        if not record(label, step.outcome, step.detail, step.saved):
            return _end(journal, result)
        grown = _new_errors(baseline, fak.error_log())
        if grown:
            record("Error.log", "review", f"Fakturama logged during '{label}': {grown}")
            return _end(journal, result)
    record("Error.log", "ok", "nothing new logged by Fakturama during the run")
    return _end(journal, result)


def _dispatch(fak: Fakturama, kind: str, arg, options: RunOptions) -> StepResult:
    if kind == "payment":
        return fak.ensure_payment(arg)
    if kind == "vat":
        return fak.ensure_vat(arg)
    if kind == "product":
        item, gross = arg
        return fak.ensure_product(item, gross)
    if kind == "debtor":
        return fak.ensure_debtor(arg, options.without_email)
    return fak.create_order(arg, options.allow_duplicate)


def _new_errors(before: str, after: str) -> str:
    """Pure: the text Fakturama appended to its Error.log (first line of it), '' if none.
    A shrunk or rotated log counts as new content."""
    if after == before:
        return ""
    new = after[len(before):] if after.startswith(before) else after
    first = next((line.strip() for line in new.splitlines() if line.strip()), "(log rotated)")
    return first[:300]


def _end(journal: Journal, result: RunResult) -> RunResult:
    final = result.final
    journal.write("end", "run", ok=result.ok, final=final.step if final else None,
                  outcome=final.outcome if final else None)
    return result


def write_report(result: RunResult, spec: OrderSpec, path: Path) -> None:
    ref = spec.order.external_reference
    lines = [f"# Run report: {ref}", "", f"Result: **{'done' if result.ok else 'stopped for manual review'}**", "",
             "| Step | Outcome | Saved | Detail |", "|---|---|---|---|"]
    for r in result.records:
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.step} | {r.outcome} | {'yes' if r.saved else ''} | {detail} |")
    lines += ["", "Journal: `journal.jsonl` (attempting/done per step). Screenshots: `*.png` before/after each "
              "step. UIA dumps on failure: `*.uia.json`.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    (path.parent / "result.json").write_text(json.dumps([vars(r) for r in result.records], indent=1),
                                             encoding="utf-8")
