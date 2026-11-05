"""Preflight: is this Fakturama in a state the automation can safely drive?

Read-only. The only actions are opening File > Preferences, switching its
pages and closing it with Cancel, and bringing the main window to the front.
Each check reports ok / failed / skipped with what was seen. The checks are
pure functions over recorded or live data, so they are tested offline.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.locators import LocatorError, Registry
from fakturama_agent.uia.money import MoneyFormat, MoneyFormatError
from fakturama_agent.uia.tree import Snapshot
from fakturama_agent.uia.winapi import WindowInfo

EXPECTED_VERSION = "2.2.0"
# Locators that exist in the main window whatever is open: proof of the English UI.
ENGLISH_KEYS = (
    "toolbar.new_order",
    "toolbar.save",
    "toolbar.new_product",
    "toolbar.new_contact",
    "menu.file",
    "menu.data",
)
_RCP_JAR = re.compile(r"^com\.sebulli\.fakturama\.rcp_(?P<version>\d+(?:\.\d+)*)(?:\.[^_]*)?\.jar$")

Status = Literal["ok", "failed", "skipped"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    seen: str
    hint: str = ""


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)
    version: str | None = None
    currency_locale: str | None = None
    money: MoneyFormat | None = None

    @property
    def ok(self) -> bool:
        return all(c.status == "ok" for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "version": self.version,
            "currency_locale": self.currency_locale,
            "money": asdict(self.money) if self.money else None,
            "checks": [asdict(c) for c in self.checks],
        }


# --------------------------------------------------------------------------
# pure checks
# --------------------------------------------------------------------------


def check_window(main: MainWindow) -> Check:
    if main.minimized:
        return Check("main window", "failed", f"pid {main.pid}, minimized", "restore (maximize) the Fakturama window")
    return Check("main window", "ok", f"pid {main.pid}, visible, workspace {main.workspace or '(none in title)'}")


def plugin_version(names: Iterable[str]) -> list[str]:
    return sorted({m["version"] for n in names if (m := _RCP_JAR.match(n))})


def check_version(install_dir: Path | None, plugin_names: Iterable[str]) -> tuple[Check, str | None]:
    if install_dir is None:
        return Check("version", "failed", "cannot read Fakturama's executable path"), None
    versions = plugin_version(plugin_names)
    if len(versions) != 1:
        return Check("version", "failed", f"com.sebulli.fakturama.rcp versions in {install_dir}: {versions or 'none'}"), None
    version = versions[0]
    if version != EXPECTED_VERSION:
        return Check("version", "failed", f"Fakturama {version}", f"the locators are for {EXPECTED_VERSION}"), version
    return Check("version", "ok", f"Fakturama {version} ({install_dir})"), version


def check_english(main_snapshot: Snapshot, registry: Registry) -> Check:
    missing = []
    for key in ENGLISH_KEYS:
        try:
            registry.resolve(main_snapshot, key)
        except LocatorError as e:
            missing.append(f"{key} ({e.kind})")
    if missing:
        return Check("English UI", "failed", "not found: " + ", ".join(missing), "switch Fakturama to English")
    return Check("English UI", "ok", f"{len(ENGLISH_KEYS)} English toolbar/menu labels found")


def check_editors(main_snapshot: Snapshot) -> Check:
    """Eclipse marks an editor with unsaved changes by a leading '*' in its tab title.
    Views and editors are both TabItems and cannot be told apart reliably, so all
    tab titles are listed and only the dirty marker decides."""
    tabs = [n.name for n in main_snapshot.root.walk() if n.control_type == "TabItem"]
    dirty = [t for t in tabs if t.startswith("*")]
    seen = f"open tabs: {tabs}" if tabs else "no open tabs"
    if dirty:
        return Check("unsaved editors", "failed", seen, f"save or close {dirty} by hand first")
    return Check("unsaved editors", "ok", seen)


def check_dialogs(main: MainWindow, windows: Iterable[WindowInfo]) -> Check:
    others = [w.title for w in windows if w.pid == main.pid and w.visible and w.hwnd != main.hwnd and w.title]
    if others:
        return Check("open dialogs", "failed", f"open: {others}", "close these dialogs (Cancel) first")
    return Check("open dialogs", "ok", "none")


def check_take_over(documents_page: Snapshot, registry: Registry) -> Check:
    name = "item-number take-over"
    try:
        box = registry.resolve(documents_page, "prefs.take_over_item_number")
    except LocatorError as e:
        return Check(name, "failed", f"checkbox not found ({e.kind})")
    seen = f"'{box.name}' is {box.toggle or 'unknown'}"
    if box.toggle != "off":
        return Check(name, "failed", seen, "Preferences > Documents: untick it, Apply and Close")
    return Check(name, "ok", seen)


def read_currency(general_page: Snapshot, registry: Registry) -> tuple[Check, str | None, MoneyFormat | None]:
    name = "currency format"
    try:
        locale = registry.resolve(general_page, "prefs.currency_locale").value
        example = registry.resolve(general_page, "prefs.currency_example").value
        decimals_text = registry.resolve(general_page, "prefs.currency_decimals").value
    except LocatorError as e:
        return Check(name, "failed", f"currency settings not found ({e.kind}: {e.key})"), None, None
    if not decimals_text or not decimals_text.strip().isdigit():
        return Check(name, "failed", f"decimal places (currency) = {decimals_text!r}"), locale, None
    try:
        money = MoneyFormat.from_example(example or "", int(decimals_text))
    except MoneyFormatError as e:
        return Check(name, "failed", f"locale {locale!r}: {e}", "the money reader does not support this format"), locale, None
    return Check(name, "ok", f"locale {locale!r}, example {example!r} -> amounts like {money.describe()}"), locale, money


def check_foreground(main: MainWindow, foreground: int) -> Check:
    if foreground != main.hwnd:
        return Check("in front", "failed", f"foreground window is {foreground:#x}", "click Fakturama, keep it in front")
    return Check("in front", "ok", "Fakturama is the foreground window")


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


class Probe(Protocol):
    """What preflight needs from the live application (faked in tests)."""

    def windows(self, pid: int) -> list[WindowInfo]: ...
    def snapshot(self, hwnd: int) -> Snapshot: ...
    def installation(self, pid: int) -> tuple[Path | None, list[str]]: ...
    def preferences(self, main: MainWindow) -> dict[str, Snapshot]: ...
    def bring_to_front(self, main: MainWindow) -> int: ...


def run_preflight(main: MainWindow, probe: Probe, registry: Registry) -> PreflightReport:
    report = PreflightReport()
    add = report.checks.append

    window = check_window(main)
    add(window)
    version, report.version = check_version(*probe.installation(main.pid))
    add(version)
    if window.status != "ok":
        for name in ("English UI", "unsaved editors", "open dialogs", "item-number take-over", "currency format", "in front"):
            add(Check(name, "skipped", "main window is minimized"))
        return report

    snap = probe.snapshot(main.hwnd)
    add(check_english(snap, registry))
    add(check_editors(snap))
    dialogs = check_dialogs(main, probe.windows(main.pid))
    add(dialogs)

    if dialogs.status != "ok":
        add(Check("item-number take-over", "skipped", "a dialog is open"))
        add(Check("currency format", "skipped", "a dialog is open"))
    else:
        try:
            pages = probe.preferences(main)
        except Exception as e:  # noqa: BLE001 - reported, never retried
            add(Check("item-number take-over", "failed", f"could not read Preferences: {e}"))
            add(Check("currency format", "failed", "could not read Preferences"))
        else:
            add(check_take_over(pages["prefs.page.documents"], registry))
            currency, report.currency_locale, report.money = read_currency(pages["prefs.page.general"], registry)
            add(currency)

    add(check_foreground(main, probe.bring_to_front(main)))
    return report


class LiveProbe:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def windows(self, pid: int) -> list[WindowInfo]:
        from fakturama_agent.uia.binding import process_windows

        return process_windows(pid)

    def snapshot(self, hwnd: int) -> Snapshot:
        from fakturama_agent.uia.tree import snapshot_hwnd

        return snapshot_hwnd(hwnd)

    def installation(self, pid: int) -> tuple[Path | None, list[str]]:
        """Install directory and its plugin file names (the version is in the jar names)."""
        from fakturama_agent.uia.winapi import process_image_path

        path = process_image_path(pid)
        if not path:
            return None, []
        install = Path(path).parent
        plugins = install / "plugins"
        return install, sorted(p.name for p in plugins.iterdir()) if plugins.is_dir() else []

    def preferences(self, main: MainWindow) -> dict[str, Snapshot]:
        from fakturama_agent.uia.prefs import read_preferences

        return read_preferences(main, self.registry)

    def bring_to_front(self, main: MainWindow) -> int:
        from fakturama_agent.uia.actions import bring_to_front
        from fakturama_agent.uia.winapi import foreground_hwnd

        bring_to_front(main.hwnd)
        return foreground_hwnd()
