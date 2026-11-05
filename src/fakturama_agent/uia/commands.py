"""UIA commands: `inspect` (dump the tree), `locate` (resolve locators live), `preflight`.

inspect/locate only read the UIA tree. preflight also opens File > Preferences,
reads it and closes it with Cancel, and brings Fakturama to the front.
Exit codes: 0 ok, 1 cannot bind Fakturama, 2 some locator/check failed.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

from fakturama_agent.uia.binding import BindError, MainWindow, find_main_window, process_windows
from fakturama_agent.uia.locators import LocatorError, Registry
from fakturama_agent.uia.preflight import LiveProbe, PreflightReport, Probe, run_preflight
from fakturama_agent.uia.tree import DEFAULT_MAX_DEPTH, DEFAULT_MAX_NODES, Snapshot, outline, redact, snapshot_hwnd

EXIT_OK, EXIT_ERROR, EXIT_UNRESOLVED = 0, 1, 2

Snapshotter = Callable[..., Snapshot]


def _bind(finder: Callable[[], MainWindow]) -> MainWindow | None:
    try:
        return finder()
    except BindError as e:
        print(f"Cannot bind Fakturama: {e}", file=sys.stderr)
        return None


def _snap(main: MainWindow, hwnd: int, snapshotter: Snapshotter, **kwargs) -> Snapshot:
    snap = snapshotter(hwnd, **kwargs)
    snap.meta.update({"window_title": main.title, "pid": main.pid})
    if main.workspace:
        redact(snap, main.workspace)
    return snap


def cmd_inspect(
    output: Path | None,
    *,
    window_regex: str | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    show_outline: bool = True,
    finder: Callable[[], MainWindow] = find_main_window,
    snapshotter: Snapshotter = snapshot_hwnd,
    window_lister=process_windows,
) -> int:
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR

    hwnd = main.hwnd
    if window_regex is not None:
        pattern = re.compile(window_regex)
        matches = [w for w in window_lister(main.pid) if pattern.search(w.title)]
        if len(matches) != 1:
            titles = [w.title for w in window_lister(main.pid) if w.title]
            print(f"Expected one Fakturama window matching {window_regex!r}, found {len(matches)}; "
                  f"visible windows: {titles}", file=sys.stderr)
            return EXIT_ERROR
        hwnd = matches[0].hwnd

    snap = _snap(main, hwnd, snapshotter, max_depth=max_depth, max_nodes=max_nodes)
    if show_outline:
        print("\n".join(outline(snap.root)))
    note = " (TRUNCATED: raise --max-depth/--max-nodes)" if snap.truncated else ""
    print(f"\n{snap.node_count} nodes{note}; workspace path shown as <workspace>")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(snap.to_json(), encoding="utf-8", newline="\n")
        print(f"Wrote {output}")
    return EXIT_OK


def cmd_locate(
    keys: list[str],
    *,
    include_unverified: bool = False,
    registry: Registry | None = None,
    finder: Callable[[], MainWindow] = find_main_window,
    snapshotter: Snapshotter = snapshot_hwnd,
    window_lister=process_windows,
) -> int:
    """Resolve locators in the window each one lives in. A locator whose dialog or
    containing menu is not open is SKIPPED (not a failure); open it and run again."""
    registry = registry or Registry.load()
    if not keys:
        keys = [k for k, loc in registry.locators.items() if include_unverified or loc.status == "verified"]
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR

    snaps: dict[str | None, Snapshot | str] = {None: _snap(main, main.hwnd, snapshotter)}

    def snapshot_for(window_regex: str | None) -> Snapshot | str:
        if window_regex not in snaps:
            pattern = re.compile(window_regex)
            found = [w for w in window_lister(main.pid) if pattern.search(w.title)]
            if not found:
                snaps[window_regex] = f"window {window_regex!r} not open"
            elif len(found) > 1:
                snaps[window_regex] = f"AMBIGUOUS: {len(found)} windows match {window_regex!r}"
            else:
                snaps[window_regex] = _snap(main, found[0].hwnd, snapshotter)
        return snaps[window_regex]

    failures = skipped = 0
    width = max(len(k) for k in keys)
    for key in keys:
        try:
            loc = registry.get(key)
            snap = snapshot_for(loc.window)
            if isinstance(snap, str):
                if snap.startswith("AMBIGUOUS"):
                    failures += 1
                    print(f"{key:<{width}}  {'AMBIGUOUS':<9}  {snap[11:]}")
                else:
                    skipped += 1
                    print(f"{key:<{width}}  {'SKIPPED':<9}  {snap}")
                continue
            node = registry.resolve(snap, key)
            patterns = ",".join(p for p in node.patterns if p != "LegacyIAccessible")
            print(f"{key:<{width}}  ok         {node.label()}  rect={node.rect}  [{patterns}]")
        except LocatorError as e:
            if e.kind == "not_found" and e.key == key and registry.get(key).transient:
                skipped += 1
                print(f"{key:<{width}}  {'SKIPPED':<9}  not open")
                continue
            if e.key != key and e.kind == "not_found":  # the containing menu/pane is not open
                skipped += 1
                print(f"{key:<{width}}  {'SKIPPED':<9}  {e.key} not open")
                continue
            failures += 1
            print(f"{key:<{width}}  {e.kind.upper():<9}  {e.detail}")
    resolved = len(keys) - failures - skipped
    print(f"\n{resolved}/{len(keys)} resolved, {failures} failed, {skipped} skipped (dialog or menu not open)")
    return EXIT_UNRESOLVED if failures else EXIT_OK


_MARK = {"ok": "ok  ", "failed": "FAIL", "skipped": "skip"}


def print_report(report: PreflightReport) -> None:
    width = max(len(c.name) for c in report.checks)
    for c in report.checks:
        print(f"[{_MARK[c.status]}] {c.name:<{width}}  {c.seen}")
        if c.status != "ok" and c.hint:
            print(f"       {'':<{width}}  -> {c.hint}")
    verdict = "READY" if report.ok else "NOT READY"
    print(f"\nPreflight: {verdict} ({sum(c.status == 'ok' for c in report.checks)}/{len(report.checks)} ok)")


def cmd_preflight(
    json_out: Path | None = None,
    *,
    registry: Registry | None = None,
    finder: Callable[[], MainWindow] = find_main_window,
    probe: Probe | None = None,
) -> int:
    registry = registry or Registry.load()
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR
    report = run_preflight(main, probe or LiveProbe(registry), registry)
    print_report(report)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        data = report.to_dict()
        if main.workspace:
            data = json.loads(json.dumps(data).replace(json.dumps(main.workspace)[1:-1], "<workspace>"))
        json_out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {json_out}")
    return EXIT_OK if report.ok else EXIT_UNRESOLVED


def format_table(read, max_width: int = 32) -> str:
    def cell(v):
        text = "" if v is None else str(v)
        return text if len(text) <= max_width else text[: max_width - 1] + "~"

    header = read.columns
    body = [[cell(row[c]) for c in header] for row in read.rows]
    widths = [max(len(h), *(len(r[i]) for r in body)) if body else len(h) for i, h in enumerate(header)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True))]
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in body]
    return "\n".join(lines)


def cmd_read_table(
    key: str,
    json_out: Path | None = None,
    *,
    registry: Registry | None = None,
    finder: Callable[[], MainWindow] = find_main_window,
    backend_factory=None,
    ocr=None,
) -> int:
    from fakturama_agent.uia.locators import LocatorError
    from fakturama_agent.uia.tables import TableError, read_table

    registry = registry or Registry.load()
    try:
        spec = registry.table(key)
    except LocatorError as e:
        print(e.detail, file=sys.stderr)
        return EXIT_ERROR
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR
    if backend_factory is None:
        from fakturama_agent.uia.views import LiveGrid

        backend_factory = LiveGrid
    if ocr is None:
        from fakturama_agent.extract.ocr import run_ocr as ocr
    try:
        read = read_table(key, spec, backend_factory(main, registry), fakturama_pid=main.pid, ocr=ocr)
    except (TableError, LocatorError, TimeoutError) as e:
        print(f"Cannot read {spec.view}: {e}", file=sys.stderr)
        return EXIT_UNRESOLVED
    where = f"{spec.view}" + (f" > {spec.filter}" if spec.filter else "")
    how = "copied twice, identical" if read.rows else "the grid is empty (judged from its pixels; not copied)"
    print(f"{where}: {len(read.rows)} row(s); header checked by OCR; {how}\n")
    if read.rows:
        print(format_table(read))
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(read.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {json_out}")
    return EXIT_OK


def print_header_checks(checks) -> None:
    width = max(len(c.field) for c in checks)
    for c in checks:
        mark = "ok  " if c.ok else "FAIL"
        print(f"[{mark}] {c.field:<{width}}  {c.seen!r}" + ("" if c.ok else f"  (expected {c.expected!r})"))


def cmd_order_open(
    spec_path: Path,
    *,
    registry: Registry | None = None,
    finder: Callable[[], MainWindow] = find_main_window,
    editor_factory=None,
) -> int:
    """Open a New Order and set its header from an OrderSpec. Never saves."""
    from pydantic import ValidationError

    from fakturama_agent.flow.order_header import HeaderError, HeaderWanted, OrderEditor, compare
    from fakturama_agent.spec.models import OrderSpec
    from fakturama_agent.uia.actions import ActionError
    from fakturama_agent.uia.datefield import DateEntryError
    from fakturama_agent.uia.inputs import InputError

    try:
        spec = OrderSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as e:
        print(f"Cannot load {spec_path}: {e}", file=sys.stderr)
        return EXIT_ERROR
    wanted = HeaderWanted(cust_ref=spec.order.external_reference, order_date=spec.order.order_date)
    registry = registry or Registry.load()
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR
    factory = editor_factory or OrderEditor.open_new
    left_open = "A New Order tab may be open: close it and answer No to 'Save changes?'."
    try:
        editor = factory(main, registry)
        print(f"Opened a New Order (proposed No. {editor.read().number}); setting the header ...")
        read = editor.set_header(wanted)
    except (HeaderError, DateEntryError, InputError, ActionError, TimeoutError) as e:
        print(f"Stopped: {e}\n{left_open}", file=sys.stderr)
        return EXIT_UNRESOLVED
    checks = compare(wanted, read)
    print()
    print_header_checks(checks)
    ok = all(c.ok for c in checks)
    print(f"\nHeader {'set and verified' if ok else 'NOT as wanted'}. Nothing was saved. {left_open}")
    return EXIT_OK if ok else EXIT_UNRESOLVED


def cmd_order_select(
    spec_path: Path,
    *,
    registry: Registry | None = None,
    finder: Callable[[], MainWindow] = find_main_window,
    editor_factory=None,
    checker=None,
    ocr=None,
) -> int:
    """Open a New Order and check the Debtor and every SKU with the selector dialogs. Never saves."""
    from pydantic import ValidationError

    from fakturama_agent.flow.order_header import HeaderError, OrderEditor
    from fakturama_agent.spec.models import OrderSpec

    try:
        spec = OrderSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as e:
        print(f"Cannot load {spec_path}: {e}", file=sys.stderr)
        return EXIT_ERROR
    registry = registry or Registry.load()
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR
    if ocr is None:
        from fakturama_agent.extract.ocr import run_ocr as ocr
    if checker is None:
        from fakturama_agent.flow.order_select import check_all as checker
    left_open = "The New Order tab is left open: close it and answer No to 'Save changes?'."
    try:
        editor = (editor_factory or OrderEditor.open_new)(main, registry)
    except (HeaderError, TimeoutError) as e:
        print(f"Cannot open a New Order: {e}", file=sys.stderr)
        return EXIT_UNRESOLVED
    print(f"Opened a New Order; checking the Debtor and {len(spec.items)} item(s) ...\n")
    outcomes = checker(editor, spec, ocr)
    width = max(len(o.what) for o in outcomes)
    for o in outcomes:
        print(f"[{'ok  ' if o.ok else 'STOP' if o.error else 'REVW' if o.decision and o.decision.needs_review else 'FAIL'}] "
              f"{o.what:<{width}}  {o.summary()}")
    skipped = len(spec.items) + 1 - len(outcomes)
    if skipped:
        print(f"       {skipped} later check(s) not run after the stop")
    ok = all(o.ok for o in outcomes) and not skipped
    print(f"\n{'All checks decided.' if ok else 'Manual review needed.'} Nothing was saved. {left_open}")
    return EXIT_OK if ok else EXIT_UNRESOLVED


def print_master_checks(checks) -> bool:
    width = max(len(c.what) for c in checks)
    for c in checks:
        mark = "ok  " if c.ok else ("STOP" if c.error else "REVW")
        print(f"[{mark}] {c.what:<{width}}  {c.summary()}")
    ok = all(c.ok for c in checks)
    print(f"\n{'All master data decided.' if ok else 'Manual review needed.'} Nothing was changed.")
    return ok


def cmd_check_master(
    *,
    rates: list | None = None,
    method: str | None = None,
    spec_path: Path | None = None,
    registry: Registry | None = None,
    finder: Callable[[], MainWindow] = find_main_window,
    vat_checker=None,
    payment_checker=None,
    ocr=None,
) -> int:
    """check-vat / check-payment / check-master: read-only master-data checks."""
    from decimal import Decimal, InvalidOperation

    from pydantic import ValidationError

    from fakturama_agent.spec.models import OrderSpec, PaymentMethod

    rates = list(rates or [])
    if spec_path is not None:
        try:
            spec = OrderSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as e:
            print(f"Cannot load {spec_path}: {e}", file=sys.stderr)
            return EXIT_ERROR
        rates = sorted({item.vat_percent for item in spec.items})
        method = spec.payment.method.value
    try:
        rates = [Decimal(str(r)) for r in rates]
        payment = PaymentMethod(method) if method is not None else None
    except (InvalidOperation, ValueError):
        known = ", ".join(m.value for m in PaymentMethod)
        print(f"Unknown rate or payment method {method!r} (known methods: {known})", file=sys.stderr)
        return EXIT_ERROR
    registry = registry or Registry.load()
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR
    if ocr is None:
        from fakturama_agent.extract.ocr import run_ocr as ocr
    if vat_checker is None or payment_checker is None:
        from fakturama_agent.flow.master_data import check_payment, check_vat

        vat_checker = vat_checker or check_vat
        payment_checker = payment_checker or check_payment
    checks = []
    for rate in rates:
        checks.append(vat_checker(main, registry, rate, ocr))
        if checks[-1].error:
            break
    if payment is not None and not any(c.error for c in checks):
        checks.append(payment_checker(main, registry, payment, ocr))
    return EXIT_OK if print_master_checks(checks) else EXIT_UNRESOLVED
