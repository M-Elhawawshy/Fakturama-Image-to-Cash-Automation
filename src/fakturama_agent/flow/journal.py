"""Run journal: one JSON line per event in ``out/runs/<timestamp>/journal.jsonl``.

Before any step that may Save, the runner writes ``attempting``; afterwards
``done`` with the outcome and whether a Save happened. A run that ended with an
``attempting`` and no ``done`` (crash, killed process), or with a stopped step
whose Save was dispatched, left a Save of unknown outcome behind. The next run for
the same Cust.Ref. refuses to start until a person has checked Fakturama
(``--checked``): a Save is never repeated blindly. A refused run records the
inherited unknown Saves (``blocked``), so rerunning without ``--checked`` never
clears them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Journal:
    path: Path

    def write(self, event: str, step: str, **data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = {"at": datetime.now().isoformat(timespec="seconds"), "event": event, "step": step, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def unresolved(events: Iterable[dict]) -> list[str]:
    """Pure: steps whose Save outcome is unknown (attempting without done, or a stop after a Save)."""
    open_steps: dict[str, dict] = {}
    unknown = []
    for e in events:
        if e["event"] == "attempting":
            open_steps[e["step"]] = e
        elif e["event"] == "blocked":  # inherited from an earlier run, not settled by this one
            unknown += [u for u in e.get("unknown", []) if u not in unknown]
        elif e["event"] == "done":
            open_steps.pop(e["step"], None)
            if e.get("outcome") == "stop" and e.get("saved"):
                unknown.append(f"{e['step']}: stopped after a Save ({e.get('detail', '')})")
    unknown += [f"{step}: attempted, never finished" for step in open_steps]
    return unknown


def previous_unresolved(runs_root: Path, cust_ref: str) -> tuple[Path, list[str]] | None:
    """The latest earlier run for ``cust_ref``, if it left an unknown Save behind (only the
    latest counts: a later run started with ``--checked`` settles the earlier ones)."""
    for path in sorted(runs_root.glob("*/journal.jsonl"), reverse=True):
        events = read_events(path)
        start = next((e for e in events if e["event"] == "start"), None)
        if not start or start.get("cust_ref") != cust_ref:
            continue
        problems = unresolved(events)
        return (path, problems) if problems else None
    return None
