"""``FakeFakturama``: the run's step interface over an in-memory workspace.

It follows the same rules as the live steps (check first, reuse an exact match,
create only what is missing, a same-name record with other details or several
matches is review, Save once) so ``run_order``'s whole decision tree can be tested
offline. Failures are injected per step kind:

- ``"review"``: the step decides review (e.g. a conflicting definition);
- ``"stop_before_save"``: the step stops before saving (nothing persisted);
- ``"stop_after_save"``: the record is persisted, then the step stops (unclear outcome);
- ``"crash"``: the adapter raises after persisting (process killed mid-step);
- ``"log_error"``: the step succeeds but Fakturama appends to its Error.log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fakturama_agent.flow.run import StepResult
from fakturama_agent.spec.derive import payment_code, vat_name
from fakturama_agent.spec.models import Item, OrderSpec, PaymentMethod


@dataclass
class FakeFakturama:
    payments: dict[str, str] = field(default_factory=lambda: {"Cash": "Mutually defined"})  # name -> code
    vats: dict[str, str] = field(default_factory=lambda: {"VAT 19%": "S"})  # name -> code
    products: dict[str, tuple[str, Decimal, str]] = field(default_factory=dict)  # sku -> (name, gross, vat)
    debtors: list[dict] = field(default_factory=list)  # Company, First Name, Name, ZIP, City
    orders: list[tuple[str, str]] = field(default_factory=list)  # (number, cust_ref)
    invoices: list[tuple[str, str, str, bool]] = field(default_factory=list)  # (number, order, cust_ref, paid)
    log: str = ""
    preflight_ok: bool = True
    currency: str | None = "EUR"  # the workspace currency (None: not determinable)
    documents_readable: bool = True
    fail: dict[str, str] = field(default_factory=dict)  # step kind -> failure mode
    calls: list[str] = field(default_factory=list)
    evidence_taken: list[str] = field(default_factory=list)
    dumps: list[str] = field(default_factory=list)

    # --- read-only ------------------------------------------------------------------------

    def preflight(self) -> StepResult:
        return StepResult("ok", "8/8 checks ok") if self.preflight_ok else StepResult("stop", "unsaved editors open")

    def workspace_currency(self) -> tuple[str | None, str]:
        return self.currency, "simulated"

    def error_log(self) -> str:
        return self.log

    def orders_with_ref(self, cust_ref: str) -> list[str] | None:
        if not self.documents_readable:
            return None
        return [n for n, ref in self.orders if ref == cust_ref]

    def invoices_with_ref(self, cust_ref: str) -> list[str] | None:
        if not self.documents_readable:
            return None
        return [n for n, _o, ref, _p in self.invoices if ref == cust_ref]

    def evidence(self, step: str, moment: str) -> None:
        self.evidence_taken.append(f"{step}:{moment}")

    def dump_on_failure(self, step: str) -> None:
        self.dumps.append(step)

    # --- steps ----------------------------------------------------------------------------------

    def _failure(self, kind: str, persist) -> StepResult | None:
        mode = self.fail.get(kind)
        if mode == "review":
            return StepResult("review", f"{kind}: conflicting definition (simulated)")
        if mode == "stop_before_save":
            return StepResult("stop", f"{kind}: field read-back differs, not saved (simulated)")
        if mode == "stop_after_save":
            persist()
            return StepResult("stop", f"{kind}: Save outcome unclear (simulated)", saved=True)
        if mode == "crash":
            persist()
            raise RuntimeError(f"{kind}: adapter crashed after Save (simulated)")
        return None

    def _created(self, kind: str, persist, detail: str) -> StepResult:
        failed = self._failure(kind, persist)
        if failed:
            return failed
        persist()
        if self.fail.get(kind) is None and self.fail.get(f"{kind}:log") == "log_error":
            self.log += f"!ENTRY {kind} NullPointerException (simulated)\n"
        return StepResult("create", detail, saved=True)

    def ensure_payment(self, method: PaymentMethod) -> StepResult:
        self.calls.append(f"payment {method.value}")
        code = payment_code(method)
        if method.value in self.payments:
            if self.payments[method.value] != code:
                return StepResult("review", f"{method.value} has code {self.payments[method.value]!r}")
            return StepResult("ok", f"{method.value} exists with {code!r}")
        return self._created("payment", lambda: self.payments.__setitem__(method.value, code), f"created {method.value}")

    def ensure_vat(self, rate: Decimal) -> StepResult:
        name = vat_name(rate)
        self.calls.append(f"vat {name}")
        if rate <= 0:
            return StepResult("review", "0% needs an exemption code")
        if name in self.vats:
            return StepResult("ok", f"{name} exists") if self.vats[name] == "S" else StepResult("review", f"{name} code")
        return self._created("vat", lambda: self.vats.__setitem__(name, "S"), f"created {name}")

    def ensure_product(self, item: Item, gross: Decimal) -> StepResult:
        self.calls.append(f"product {item.sku}")
        vat = vat_name(item.vat_percent)
        if vat not in self.vats:
            return StepResult("stop", f"{vat} is missing")
        want = (item.description, gross, vat)
        if item.sku in self.products:
            if self.products[item.sku] != want:
                return StepResult("review", f"{item.sku} exists with other details")
            return StepResult("ok", f"{item.sku} exists")
        return self._created("product", lambda: self.products.__setitem__(item.sku, want), f"created {item.sku}")

    def ensure_debtor(self, spec: OrderSpec, without_email: bool) -> StepResult:
        self.calls.append(f"debtor {spec.debtor.company}")
        key = {"Company": spec.debtor.company, "First Name": spec.debtor.contact_first_name,
               "Name": spec.debtor.contact_last_name, "ZIP": spec.billing_address.zip, "City": spec.billing_address.city}
        exact = [d for d in self.debtors if d == key]
        same = [d for d in self.debtors if d["Company"] == key["Company"]]
        if len(exact) > 1:
            return StepResult("review", f"{len(exact)} contacts match")
        if exact:
            return StepResult("ok", "exists, reused")
        if same:
            return StepResult("review", f"{key['Company']!r} exists with other details")
        if spec.payment.method.value not in self.payments:
            return StepResult("stop", "its term of payment is missing")
        if (spec.debtor.email or "").endswith(".test") and not without_email:
            return StepResult("review", f"Fakturama rejects the e-mail {spec.debtor.email!r}; nothing saved")
        return self._created("debtor", lambda: self.debtors.append(dict(key)), "created the Debtor")

    def create_order(self, spec: OrderSpec, allow_duplicate: bool) -> StepResult:
        self.calls.append("order")
        ref = spec.order.external_reference
        if self.orders_with_ref(ref) and not allow_duplicate:
            return StepResult("review", "duplicate guard")
        missing = [i.sku for i in spec.items if i.sku not in self.products]
        if missing or not any(d["Company"] == spec.debtor.company for d in self.debtors):
            return StepResult("review", f"not found in the Order's dialogs: {missing or 'Debtor'}")
        number = f"PO{len(self.orders) + 1:06d}"
        result = self._created("order", lambda: self.orders.append((number, ref)), f"{number} saved and verified")
        result.ref = number
        return result

    def create_invoice(self, spec: OrderSpec, allow_duplicate: bool, order_number: str) -> StepResult:
        self.calls.append(f"invoice of {order_number}")
        ref = spec.order.external_reference
        if self.invoices_with_ref(ref) and not allow_duplicate:
            return StepResult("review", "invoice guard")
        if (order_number, ref) not in self.orders:
            return StepResult("stop", f"the saved Order {order_number} is not verified")
        if spec.payment.method.value not in self.payments:
            return StepResult("review", f"{spec.payment.method.value} is not available")
        number = f"INV{len(self.invoices) + 1:06d}"
        paid = spec.payment.paid_status.value == "PAID"
        result = self._created("invoice", lambda: self.invoices.append((number, order_number, ref, paid)),
                               f"{number} of {order_number} saved, {'paid' if paid else 'not paid'}, verified")
        result.ref = number
        return result
