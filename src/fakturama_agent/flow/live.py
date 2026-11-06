"""``LiveFakturama``: the run's step interface over the real application.

Each method delegates to the step built and live-tested in parts B-I and maps its
result onto ok / create / review / stop. Evidence goes to the run's folder: a
screenshot of the Fakturama window before and after every step, and a UIA dump of
the main window when a step ends in review or stop.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from fakturama_agent.flow.create_master import Created
from fakturama_agent.flow.run import StepResult
from fakturama_agent.spec.models import Item, OrderSpec, PaymentMethod
from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.locators import Registry

ERROR_LOG = Path.home() / ".fakturama2" / "Logs" / "Error.log"
_OUTCOME = {"exists": "ok", "created": "create", "review": "review", "stopped": "stop"}


def from_created(c: Created) -> StepResult:
    return StepResult(_OUTCOME[c.status], c.detail, c.saved)


def from_order_run(run) -> StepResult:
    """Pure: an ``OrderRun`` of create_order as one step result."""
    text = "; ".join(f"{s.name}: {s.detail}" for s in run.steps if not s.ok) or \
        "; ".join(f"{s.name}: {s.detail}" for s in run.steps)
    if run.ok:
        return StepResult("create", f"{run.number} saved and verified ({run.steps[-1].detail})", saved=True,
                          ref=run.number)
    if run.saved:
        return StepResult("stop", text, saved=True)
    failed = next(s for s in run.steps if not s.ok)
    left = " The unsaved New Order is left open." if run.unsaved_left_open else ""
    return StepResult("stop" if failed.name == "build the Order" else "review", text + left)


class LiveFakturama:
    def __init__(self, main: MainWindow, registry: Registry, ocr: Callable, evidence_dir: Path,
                 error_log: Path = ERROR_LOG) -> None:
        self.main, self.registry, self.ocr = main, registry, ocr
        self.dir = evidence_dir
        self.error_log_path = error_log
        self.money = None
        self.currency_locale = None
        self._n = 0

    # --- read-only ------------------------------------------------------------------------

    def preflight(self) -> StepResult:
        from fakturama_agent.uia.preflight import LiveProbe, run_preflight

        report = run_preflight(self.main, LiveProbe(self.registry), self.registry)
        self.money = report.money
        self.currency_locale = report.currency_locale
        failed = [f"{c.name}: {c.seen}" for c in report.checks if c.status == "failed"]
        if not report.ok:
            return StepResult("stop", "; ".join(failed) or "not ready")
        return StepResult("ok", f"{sum(c.status == 'ok' for c in report.checks)}/{len(report.checks)} checks ok; "
                                f"money like {report.money.describe()}")

    def workspace_currency(self) -> tuple[str | None, str]:
        from fakturama_agent.uia.money import workspace_currency

        return workspace_currency(self.currency_locale, self.money)

    def error_log(self) -> str:
        try:
            return self.error_log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def orders_with_ref(self, cust_ref: str) -> list[str] | None:
        from fakturama_agent.flow.order_lines import duplicates
        from fakturama_agent.uia.record_editor import read_view

        try:
            _grid, docs = read_view(self.main, self.registry, "documents", self.ocr)
        except Exception:  # noqa: BLE001 - unreadable is a stop, never "no orders"
            return None
        return duplicates(docs.rows, cust_ref)

    def invoices_with_ref(self, cust_ref: str) -> list[str] | None:
        from fakturama_agent.flow.invoice import invoices_with_ref

        return invoices_with_ref(self.main, self.registry, self.ocr, cust_ref)

    def evidence(self, step: str, moment: str) -> None:
        from PIL import ImageGrab

        from fakturama_agent.uia.winapi import window_rect

        self._n += moment == "before"
        slug = re.sub(r"[^A-Za-z0-9]+", "-", step).strip("-").lower()
        try:
            ImageGrab.grab(bbox=window_rect(self.main.hwnd), all_screens=True).save(
                self.dir / f"{self._n:02d}-{slug}-{moment}.png")
        except Exception:  # noqa: BLE001 - evidence must never break a run
            pass

    def dump_on_failure(self, step: str) -> None:
        from fakturama_agent.uia.tree import redact, snapshot_hwnd

        slug = re.sub(r"[^A-Za-z0-9]+", "-", step).strip("-").lower()
        try:
            snap = snapshot_hwnd(self.main.hwnd)
            if self.main.workspace:
                redact(snap, self.main.workspace)
            (self.dir / f"{slug}.uia.json").write_text(snap.to_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # --- steps ------------------------------------------------------------------------------------

    def ensure_payment(self, method: PaymentMethod) -> StepResult:
        from fakturama_agent.flow.create_master import create_payment

        return from_created(create_payment(self.main, self.registry, method, self.ocr))

    def ensure_vat(self, rate: Decimal) -> StepResult:
        from fakturama_agent.flow.create_master import create_vat

        return from_created(create_vat(self.main, self.registry, rate, self.ocr))

    def ensure_product(self, item: Item, gross: Decimal) -> StepResult:
        from fakturama_agent.flow.create_master import create_product

        return from_created(create_product(self.main, self.registry, item, gross, self.money, self.ocr))

    def ensure_debtor(self, spec: OrderSpec, without_email: bool) -> StepResult:
        from fakturama_agent.flow.create_debtor import create_debtor

        return from_created(create_debtor(self.main, self.registry, spec, self.ocr, without_email=without_email))

    def create_order(self, spec: OrderSpec, allow_duplicate: bool) -> StepResult:
        from fakturama_agent.flow.create_order import create_order

        return from_order_run(create_order(self.main, self.registry, spec, self.money, self.ocr,
                                           allow_duplicate=allow_duplicate))

    def create_invoice(self, spec: OrderSpec, allow_duplicate: bool, order_number: str | None) -> StepResult:
        from fakturama_agent.flow.invoice import create_invoice

        return from_invoice_run(create_invoice(self.main, self.registry, spec, self.money, self.ocr,
                                               allow_duplicate=allow_duplicate, order_number=order_number))


def from_invoice_run(run) -> StepResult:
    """Pure: an ``InvoiceRun`` of create_invoice as one step result."""
    failed = [s for s in run.steps if not s.ok]
    text = "; ".join(f"{s.name}: {s.detail}" for s in failed) or "; ".join(f"{s.name}: {s.detail}" for s in run.steps)
    if run.ok:
        paid = next((s.detail for s in run.steps if s.name == "saved Invoice reopened"), "")
        return StepResult("create", f"{run.number} of {run.order_number} saved and verified ({paid})", saved=True,
                          ref=run.number)
    if run.saved:
        return StepResult("stop", text, saved=True, ref=run.number)
    left = " The unsaved New Invoice is left open." if run.invoice is not None else ""
    hard = failed and failed[0].name in ("linked Invoice", "save Invoice")  # an error, not a mismatch
    return StepResult("stop" if hard else "review", text + left)
