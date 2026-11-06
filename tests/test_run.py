"""Part J: the run's decision tree on the simulator, the journal, the report."""

from decimal import Decimal
from pathlib import Path

import pytest

from fakturama_agent.flow.journal import Journal, previous_unresolved, read_events, unresolved
from fakturama_agent.flow.run import RunOptions, _new_errors, plan, run_order, spec_gate, write_report
from fakturama_agent.flow.simulator import FakeFakturama
from fakturama_agent.spec.models import CheckStatus, FieldCheck, OrderSpec, Provenance

GOLDEN = Path(__file__).parent / "golden" / "order_001.json"
SPEC = OrderSpec.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
REF = SPEC.order.external_reference
NORTHSTAR = {"Company": "Northstar Office GmbH", "First Name": "Marta", "Name": "Klein", "ZIP": "10117", "City": "Berlin"}


def complete() -> FakeFakturama:
    """A workspace that already holds everything the sample needs."""
    return FakeFakturama(
        payments={"Cash": "Mutually defined", "Bank Transfer": "Credit transfer"},
        products={"CHR-ERG-01": ("Ergonomic Desk Chair", Decimal("297.50"), "VAT 19%"),
                  "MAT-DESK-02": ("Anti-Fatigue Desk Mat", Decimal("47.60"), "VAT 19%")},
        debtors=[dict(NORTHSTAR)],
    )


def go(fak, tmp_path, n=1, **options):
    runs = tmp_path / "runs"
    journal = Journal(runs / f"2026110{n}T000000" / "journal.jsonl")
    result = run_order(SPEC, fak, journal, runs, RunOptions(**options))
    return result, read_events(journal.path)


def outcomes(result):
    return [(r.step, r.outcome) for r in result.records]


def test_plan_order_is_payment_vat_products_debtor():
    assert [label for _, label, _ in plan(SPEC)] == [
        "Payment Bank Transfer", "VAT 19%", "Product CHR-ERG-01", "Product MAT-DESK-02", "Debtor Northstar Office GmbH"]


def test_all_missing_creates_everything(tmp_path):
    fak = FakeFakturama()
    result, events = go(fak, tmp_path, without_email=True)
    assert result.ok
    assert [o for _, o in outcomes(result)][4:] == ["create", "ok", "create", "create", "create", "create", "create",
                                                    "ok"]
    assert fak.orders == [("PO000001", REF)] and fak.invoices == [("INV000001", "PO000001", REF, True)]
    assert sum(e["event"] == "attempting" for e in events) == sum(e["event"] == "done" for e in events) == 7
    assert "Order WEB-2026-0714-A17:after" in fak.evidence_taken and fak.dumps == []


def test_all_exist_creates_only_the_order(tmp_path):
    fak = complete()
    result, _ = go(fak, tmp_path)
    assert result.ok
    assert [o for s, o in outcomes(result) if s.startswith(("Payment", "VAT", "Product", "Debtor"))] == ["ok"] * 5
    assert outcomes(result)[-3:] == [("Order WEB-2026-0714-A17", "create"), ("Invoice WEB-2026-0714-A17", "create"),
                                     ("Error.log", "ok")]


def test_second_run_stops_at_the_duplicate_guard(tmp_path):
    fak = complete()
    go(fak, tmp_path, n=1)
    result, _ = go(fak, tmp_path, n=2)
    assert outcomes(result)[-1] == ("duplicate guard", "review") and len(fak.orders) == 1
    result, _ = go(fak, tmp_path, n=3, allow_duplicate=True)
    assert result.ok and len(fak.orders) == 2


def test_ambiguous_debtor_is_review_and_nothing_after_it(tmp_path):
    fak = complete()
    fak.debtors.append(dict(NORTHSTAR))
    result, _ = go(fak, tmp_path)
    assert result.final.step == "Debtor Northstar Office GmbH" and result.final.outcome == "review"
    assert fak.orders == [] and "order" not in fak.calls and fak.dumps == ["Debtor Northstar Office GmbH"]


def test_conflicting_debtor_and_rejected_email(tmp_path):
    fak = complete()
    fak.debtors = [{**NORTHSTAR, "ZIP": "99999"}]
    assert go(fak, tmp_path)[0].final.outcome == "review"
    fak = complete()
    fak.debtors = []
    result, _ = go(fak, tmp_path, n=2)
    assert result.final.outcome == "review" and "rejects the e-mail" in result.final.detail and fak.debtors == []


def test_conflicting_payment_term_stops_before_anything_is_created(tmp_path):
    fak = FakeFakturama(payments={"Bank Transfer": ""})
    result, _ = go(fak, tmp_path)
    assert result.final.step == "Payment Bank Transfer" and result.final.outcome == "review"
    assert fak.vats == {"VAT 19%": "S"} and fak.products == {}


def test_failure_after_a_master_save_blocks_the_next_run(tmp_path):
    fak = FakeFakturama(fail={"product": "stop_after_save"})
    result, events = go(fak, tmp_path, without_email=True)
    assert result.final.outcome == "stop" and result.final.saved and "CHR-ERG-01" in fak.products
    assert unresolved(events)  # stopped after a Save
    fak.fail = {}
    blocked, _ = go(fak, tmp_path, n=2, without_email=True)
    assert outcomes(blocked)[-1] == ("journal", "review") and "--checked" in blocked.final.detail
    resumed, _ = go(fak, tmp_path, n=3, without_email=True, checked=True)
    assert resumed.ok and len(fak.products) == 2  # the product saved earlier is reused, not duplicated
    assert previous_unresolved(tmp_path / "runs", REF) is None


def test_crash_after_the_order_save_leaves_attempting_open(tmp_path):
    fak = complete()
    fak.fail = {"order": "crash"}
    result, events = go(fak, tmp_path)
    assert result.final.outcome == "stop" and len(fak.orders) == 1
    assert unresolved(events) == ["Order WEB-2026-0714-A17: attempted, never finished"]
    fak.fail = {}
    blocked, _ = go(fak, tmp_path, n=2)
    assert blocked.final.step == "journal" and len(fak.orders) == 1
    checked, _ = go(fak, tmp_path, n=3, checked=True)
    assert checked.ok and len(fak.orders) == 1  # the saved Order is found, not repeated: resume with its Invoice
    assert ("duplicate guard", "ok") in outcomes(checked) and "resuming" in checked.records[3].detail
    assert fak.invoices == [("INV000001", "PO000001", REF, True)]


def test_stop_before_save_does_not_block_the_next_run(tmp_path):
    fak = complete()
    fak.fail = {"order": "stop_before_save"}
    result, events = go(fak, tmp_path)
    assert result.final.outcome == "stop" and not result.final.saved and unresolved(events) == []
    fak.fail = {}
    assert go(fak, tmp_path, n=2)[0].ok


def test_new_error_log_entry_stops_the_run(tmp_path):
    fak = FakeFakturama(log="old entry\n", fail={"vat:log": "log_error"})
    fak.payments["Bank Transfer"] = "Credit transfer"
    fak.vats = {}
    result, _ = go(fak, tmp_path)
    assert result.final.step == "Error.log" and "NullPointerException" in result.final.detail
    assert "product CHR-ERG-01" not in fak.calls


def test_gates_before_any_ui_step(tmp_path):
    fak = complete()
    fak.preflight_ok = False
    assert go(fak, tmp_path)[0].final.step == "preflight" and fak.calls == []
    fak = complete()
    fak.documents_readable = False
    assert go(fak, tmp_path, n=2)[0].final.outcome == "stop" and fak.calls == []
    ok = FieldCheck(field="order.external_reference", value=REF, status=CheckStatus.CONFIRMED, critical=True)
    soft = FieldCheck(field="items[0].unit", value="pcs", status=CheckStatus.UNCONFIRMED, critical=False)
    spec = SPEC.model_copy(update={"provenance": Provenance(field_checks=[ok, soft])})
    assert spec_gate(spec) == []
    bad = ok.model_copy(update={"status": CheckStatus.CONFLICT})
    spec = SPEC.model_copy(update={"provenance": Provenance(field_checks=[bad, soft])})
    assert spec_gate(spec) == [f"order.external_reference = {REF!r} is conflict"]
    fak = complete()
    journal = Journal(tmp_path / "runs" / "x" / "journal.jsonl")
    result = run_order(spec, fak, journal, tmp_path / "runs")
    assert result.final.step == "spec" and result.final.outcome == "review" and fak.calls == []


def test_new_errors_text():
    assert _new_errors("a\n", "a\n") == ""
    assert _new_errors("a\n", "a\nb boom\n") == "b boom"
    assert _new_errors("a long log\n", "rotated\n") == "rotated"


def test_report_lists_every_step(tmp_path):
    fak = complete()
    result, _ = go(fak, tmp_path)
    report = tmp_path / "report.md"
    write_report(result, SPEC, report)
    text = report.read_text(encoding="utf-8")
    assert "Result: **done**" in text and "| Order WEB-2026-0714-A17 | create | yes |" in text
    assert (tmp_path / "result.json").exists()


@pytest.mark.parametrize("mode", ["review", "stop_before_save"])
def test_debtor_failures_never_reach_the_order(tmp_path, mode):
    fak = complete()
    fak.debtors = []
    fak.fail = {"debtor": mode}
    result, _ = go(fak, tmp_path, without_email=True)
    assert result.final.step.startswith("Debtor") and "order" not in fak.calls


# --- the command, with the simulator as adapter --------------------------------------------------


def test_run_command_writes_journal_and_report(tmp_path, capsys):
    from fakturama_agent.uia.commands import cmd_run

    fak = FakeFakturama()
    code = cmd_run(GOLDEN, without_email=True, runs_root=tmp_path / "runs", adapter_factory=lambda d: fak)
    out = capsys.readouterr().out
    assert code == 0 and "Done: the Order and its linked Invoice are saved and verified." in out and "[NEW ] Order" in out
    run_dir = next((tmp_path / "runs").iterdir())
    assert {p.name for p in run_dir.iterdir()} >= {"journal.jsonl", "report.md", "result.json"}
    code = cmd_run(GOLDEN, runs_root=tmp_path / "runs", adapter_factory=lambda d: fak)
    assert code == 2 and "duplicate guard" in capsys.readouterr().out


def test_run_command_after_an_unclear_save(tmp_path, capsys):
    from fakturama_agent.uia.commands import cmd_run

    fak = complete()
    fak.fail = {"order": "stop_after_save"}
    assert cmd_run(GOLDEN, runs_root=tmp_path / "runs", adapter_factory=lambda d: fak) == 2
    assert "the next run needs --checked" in capsys.readouterr().out


# --- live adapter mapping (pure parts) ------------------------------------------------------------


def test_order_run_mapping():
    from fakturama_agent.flow.create_order import OrderRun
    from fakturama_agent.flow.live import from_created, from_order_run
    from fakturama_agent.flow.create_master import Created

    done = OrderRun(number="PO000005", saved=True)
    done.add("save", [], "saved once")
    done.add("Documents", [], "PO000005: ok")
    assert from_order_run(done).outcome == "create" and from_order_run(done).saved
    dup = OrderRun()
    dup.add("duplicate guard", ["exists"], "")
    assert from_order_run(dup).outcome == "review"
    built = OrderRun(unsaved_left_open=True)
    built.add("build the Order", ["TimeoutError"], "")
    assert from_order_run(built).outcome == "stop" and "left open" in from_order_run(built).detail
    unclear = OrderRun(saved=True)
    unclear.add("save", ["outcome unclear"], "")
    assert from_order_run(unclear).outcome == "stop" and from_order_run(unclear).saved
    assert from_created(Created("x", "exists", "d")).outcome == "ok"
    assert from_created(Created("x", "stopped", "d", saved=True)).saved


def test_live_error_log_reading(tmp_path):
    from fakturama_agent.flow.live import LiveFakturama

    log = tmp_path / "Error.log"
    fak = LiveFakturama(main=None, registry=None, ocr=None, evidence_dir=tmp_path, error_log=log)
    assert fak.error_log() == ""
    log.write_text("!ENTRY boom\n", encoding="utf-8")
    assert fak.error_log() == "!ENTRY boom\n"



# --- Phase 4: the Invoice step ---------------------------------------------------------------------------


def test_second_run_after_the_invoice_is_done(tmp_path):
    fak = complete()
    go(fak, tmp_path, n=1)
    result, _ = go(fak, tmp_path, n=2)
    assert outcomes(result)[-1] == ("duplicate guard", "review") and "this order is done" in result.final.detail
    assert len(fak.orders) == len(fak.invoices) == 1


def test_resume_invoices_an_existing_order_without_master_steps(tmp_path):
    fak = complete()
    fak.orders = [("PO000006", REF)]
    result, _ = go(fak, tmp_path)
    assert result.ok and fak.calls == ["invoice of PO000006"]
    assert fak.invoices == [("INV000001", "PO000006", REF, True)]


def test_two_orders_without_invoice_is_review(tmp_path):
    fak = complete()
    fak.orders = [("PO000005", REF), ("PO000006", REF)]
    result, _ = go(fak, tmp_path)
    assert result.final.step == "duplicate guard" and result.final.outcome == "review" and fak.calls == []


def test_override_invoices_the_new_order(tmp_path):
    fak = complete()
    go(fak, tmp_path, n=1)
    result, _ = go(fak, tmp_path, n=2, allow_duplicate=True)
    assert result.ok and fak.invoices[-1][:2] == ("INV000002", "PO000002")


def test_unpaid_order_gets_an_unpaid_invoice(tmp_path):
    from fakturama_agent.spec.models import PaidStatus

    unpaid = SPEC.model_copy(update={"payment": SPEC.payment.model_copy(
        update={"paid_status": PaidStatus.UNPAID, "payment_date": None})})
    fak = complete()
    runs = tmp_path / "runs"
    result = run_order(unpaid, fak, Journal(runs / "a" / "journal.jsonl"), runs)
    assert result.ok and fak.invoices[0][3] is False and "not paid" in result.records[-2].detail


def test_failure_after_the_invoice_save_blocks_then_is_found(tmp_path):
    fak = complete()
    fak.fail = {"invoice": "stop_after_save"}
    result, events = go(fak, tmp_path)
    assert result.final.step.startswith("Invoice") and result.final.saved and len(fak.invoices) == 1
    assert unresolved(events)
    fak.fail = {}
    assert go(fak, tmp_path, n=2)[0].final.step == "journal"
    checked, _ = go(fak, tmp_path, n=3, checked=True)
    assert checked.final.step == "duplicate guard" and len(fak.invoices) == 1  # never a second Invoice


def test_invoice_review_leaves_the_saved_order(tmp_path):
    fak = complete()
    fak.fail = {"invoice": "review"}
    result, _ = go(fak, tmp_path)
    assert result.final.outcome == "review" and len(fak.orders) == 1 and fak.invoices == []
    fak.fail = {}
    resumed, _ = go(fak, tmp_path, n=2)
    assert resumed.ok and len(fak.orders) == 1 and len(fak.invoices) == 1


def test_invoice_run_mapping():
    from fakturama_agent.flow.create_order import Step
    from fakturama_agent.flow.invoice import InvoiceRun
    from fakturama_agent.flow.live import from_invoice_run

    done = InvoiceRun([Step("saved Invoice reopened", True, "Bank Transfer, paid on")], "PO000006", saved=True,
                      number="INV000001")
    r = from_invoice_run(done)
    assert (r.outcome, r.saved, r.ref) == ("create", True, "INV000001") and "paid on" in r.detail
    guard = InvoiceRun([Step("invoice guard", False, "exists")])
    assert from_invoice_run(guard).outcome == "review"
    mismatch = InvoiceRun([Step("Invoice lines", False, "line 1 differs")], invoice=object())
    assert from_invoice_run(mismatch).outcome == "review" and "left open" in from_invoice_run(mismatch).detail
    error = InvoiceRun([Step("linked Invoice", False, "TimeoutError")])
    assert from_invoice_run(error).outcome == "stop"
    unclear = InvoiceRun([Step("save Invoice", False, "unclear")], saved=True)
    assert from_invoice_run(unclear).outcome == "stop" and from_invoice_run(unclear).saved


def test_rerunning_without_checked_never_clears_the_block(tmp_path):
    fak = complete()
    fak.fail = {"order": "crash"}
    go(fak, tmp_path, n=1)
    fak.fail = {}
    for n in (2, 3, 4):
        blocked, _ = go(fak, tmp_path, n=n)
        assert blocked.final.step == "journal" and blocked.final.outcome == "review"
    assert go(fak, tmp_path, n=5, checked=True)[0].ok
    assert go(fak, tmp_path, n=6)[0].final.step == "duplicate guard"  # settled: the order is done
