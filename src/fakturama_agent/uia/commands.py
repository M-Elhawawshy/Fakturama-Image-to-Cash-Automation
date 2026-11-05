"""UIA commands: `inspect` (dump the tree) and `locate` (resolve locators live).

Both are read-only: they bind Fakturama and read its UIA tree, nothing else.
Exit codes: 0 ok, 1 cannot bind Fakturama, 2 some locator did not resolve.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

from fakturama_agent.uia.binding import BindError, MainWindow, find_main_window, process_windows
from fakturama_agent.uia.locators import LocatorError, Registry
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
) -> int:
    registry = registry or Registry.load()
    if not keys:
        keys = [k for k, loc in registry.locators.items() if include_unverified or loc.status == "verified"]
    main = _bind(finder)
    if main is None:
        return EXIT_ERROR
    snap = _snap(main, main.hwnd, snapshotter)

    failures = 0
    width = max(len(k) for k in keys)
    for key in keys:
        try:
            node = registry.resolve(snap, key)
            patterns = ",".join(p for p in node.patterns if p != "LegacyIAccessible")
            print(f"{key:<{width}}  ok         {node.label()}  rect={node.rect}  [{patterns}]")
        except LocatorError as e:
            failures += 1
            print(f"{key:<{width}}  {e.kind.upper():<9}  {e.detail}")
    print(f"\n{len(keys) - failures}/{len(keys)} resolved in the main window")
    return EXIT_UNRESOLVED if failures else EXIT_OK
