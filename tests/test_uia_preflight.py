"""Preflight checks on recorded trees and a fake probe (no Fakturama needed)."""

from dataclasses import replace
from pathlib import Path

import pytest

from fakturama_agent.uia.binding import MainWindow
from fakturama_agent.uia.commands import cmd_preflight
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.money import MoneyFormat
from fakturama_agent.uia.preflight import (
    check_dialogs,
    check_editors,
    check_english,
    check_foreground,
    check_take_over,
    check_version,
    check_window,
    plugin_version,
    read_currency,
    run_preflight,
)
from fakturama_agent.uia.tree import Snapshot, UiNode
from fakturama_agent.uia.winapi import WindowInfo

FIX = Path(__file__).parent / "fixtures" / "uia"
MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama - C:\\ws", workspace="C:\\ws", minimized=False)
PLUGINS_220 = ["com.sebulli.fakturama.rcp_2.2.0.jar", "com.sebulli.fakturama.model_2.2.0.jar", "org.eclipse.ui_3.1.jar"]


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


def snap(name: str) -> Snapshot:
    return Snapshot.load(FIX / f"{name}.json")


def by_name(snapshot: Snapshot, control_type: str, name: str) -> UiNode:
    (node,) = [n for n in snapshot.root.walk() if n.control_type == control_type and n.name == name]
    return node


# --- single checks -----------------------------------------------------------


def test_window():
    assert check_window(MAIN).status == "ok"
    failed = check_window(replace(MAIN, minimized=True))
    assert failed.status == "failed" and "restore" in failed.hint


def test_version_from_plugin_jars():
    assert plugin_version(PLUGINS_220) == ["2.2.0"]
    assert plugin_version(["com.sebulli.fakturama.rcp_2.3.1.v2024.jar"]) == ["2.3.1"]
    check, version = check_version(Path("C:/Program Files/Fakturama2"), PLUGINS_220)
    assert (check.status, version) == ("ok", "2.2.0")
    check, version = check_version(Path("X"), ["com.sebulli.fakturama.rcp_2.3.0.jar"])
    assert (check.status, version) == ("failed", "2.3.0")
    assert check_version(Path("X"), ["com.sebulli.fakturama.rcp_2.2.0.jar", "com.sebulli.fakturama.rcp_2.3.0.jar"])[0].status == "failed"
    assert check_version(Path("X"), [])[0].status == "failed"
    assert check_version(None, [])[0].status == "failed"


def test_english_on_recorded_main_window(registry):
    assert check_english(snap("main_window"), registry).status == "ok"


def test_german_ui_fails_english(registry):
    main = snap("main_window")
    by_name(main, "Button", "Create: New Order").name = "Neu: Auftrag"
    check = check_english(main, registry)
    assert check.status == "failed" and "toolbar.new_order" in check.seen


def test_editors_recorded_are_clean():
    check = check_editors(snap("main_window"))
    assert check.status == "ok"
    assert "Bank Transfer" in check.seen and "Documents" in check.seen


def test_dirty_editor_fails():
    main = snap("main_window")
    by_name(main, "TabItem", "Cash").name = "*Cash"
    check = check_editors(main)
    assert check.status == "failed" and "*Cash" in check.hint


def win(hwnd, title, pid=100, visible=True):
    return WindowInfo(hwnd, pid, "SWT_Window0", title, visible, False, "Fakturama.exe")


def test_dialogs():
    helpers = [win(3, MAIN.title), win(6, ""), win(7, "SWT_Window_Fakturama", visible=False), win(8, "Other", pid=5)]
    assert check_dialogs(MAIN, helpers).status == "ok"
    check = check_dialogs(MAIN, [*helpers, win(9, "Select the address")])
    assert check.status == "failed" and "Select the address" in check.seen


def test_take_over_recorded_off(registry):
    check = check_take_over(snap("prefs_documents"), registry)
    assert check.status == "ok" and "is off" in check.seen


@pytest.mark.parametrize("state", ["on", "indeterminate", None])
def test_take_over_not_off_fails(registry, state):
    page = snap("prefs_documents")
    by_name(page, "CheckBox", "immediately take over a clearly found item number").toggle = state
    check = check_take_over(page, registry)
    assert check.status == "failed" and "untick" in check.hint


def test_take_over_missing_on_general_page(registry):
    assert check_take_over(snap("prefs_general"), registry).status == "failed"


def test_currency_recorded_us(registry):
    check, locale, money = read_currency(snap("prefs_general"), registry)
    assert check.status == "ok"
    assert locale == "United States"
    assert money == MoneyFormat("$", True, ",", ".", 2)


def test_currency_german_example(registry):
    page = snap("prefs_general")
    by_name(page, "ComboBox", "Currency locale").value = "Germany"
    by_name(page, "Edit", "Example").value = "-1.234,57 €"
    check, locale, money = read_currency(page, registry)
    assert (check.status, locale, money.decimal, money.symbol) == ("ok", "Germany", ",", "€")


def test_currency_unreadable_example(registry):
    page = snap("prefs_general")
    by_name(page, "Edit", "Example").value = "???"
    check, _, money = read_currency(page, registry)
    assert check.status == "failed" and money is None


def test_foreground():
    assert check_foreground(MAIN, 3).status == "ok"
    assert check_foreground(MAIN, 99).status == "failed"


# --- orchestration -------------------------------------------------------------


class FakeProbe:
    def __init__(self, *, windows=None, main_snapshot=None, prefs_error=None, foreground=3, plugins=PLUGINS_220):
        self._windows = windows if windows is not None else [win(3, MAIN.title)]
        self._main = main_snapshot or snap("main_window")
        self.prefs_error = prefs_error
        self.prefs_opened = 0
        self.foreground = foreground
        self.plugins = plugins

    def windows(self, pid):
        return self._windows

    def snapshot(self, hwnd):
        assert hwnd == 3
        return self._main

    def installation(self, pid):
        return Path("C:/Program Files/Fakturama2"), self.plugins

    def preferences(self, main):
        self.prefs_opened += 1
        if self.prefs_error:
            raise self.prefs_error
        return {"prefs.page.general": snap("prefs_general"), "prefs.page.documents": snap("prefs_documents")}

    def bring_to_front(self, main):
        return self.foreground


def statuses(report):
    return {c.name: c.status for c in report.checks}


def test_all_ok_on_recordings(registry):
    report = run_preflight(MAIN, FakeProbe(), registry)
    assert report.ok, report.checks
    assert report.version == "2.2.0" and report.currency_locale == "United States"
    assert report.money.parse("$678.30") == report.money.parse("678.30")
    assert len(report.checks) == 8


def test_open_dialog_skips_preferences(registry):
    probe = FakeProbe(windows=[win(3, MAIN.title), win(9, "Select the address")])
    report = run_preflight(MAIN, probe, registry)
    assert probe.prefs_opened == 0
    s = statuses(report)
    assert s["open dialogs"] == "failed" and s["item-number take-over"] == "skipped" and s["currency format"] == "skipped"
    assert report.money is None and not report.ok


def test_wrong_version_is_reported(registry):
    report = run_preflight(MAIN, FakeProbe(plugins=["com.sebulli.fakturama.rcp_2.3.0.jar"]), registry)
    assert statuses(report)["version"] == "failed" and report.version == "2.3.0"


def test_minimized_skips_live_checks(registry):
    probe = FakeProbe()
    report = run_preflight(replace(MAIN, minimized=True), probe, registry)
    assert probe.prefs_opened == 0
    assert statuses(report)["English UI"] == "skipped" and not report.ok


def test_preferences_error_is_reported_not_raised(registry):
    report = run_preflight(MAIN, FakeProbe(prefs_error=TimeoutError("no dialog")), registry)
    s = statuses(report)
    assert s["item-number take-over"] == "failed" and "no dialog" in report.checks[5].seen


def test_cmd_preflight_prints_and_writes_json(registry, tmp_path, capsys):
    out = tmp_path / "pre.json"
    code = cmd_preflight(out, registry=registry, finder=lambda: MAIN, probe=FakeProbe())
    printed = capsys.readouterr().out
    assert code == 0 and "Preflight: READY (8/8 ok)" in printed
    assert "[ok  ] currency format" in printed
    data = out.read_text(encoding="utf-8")
    assert '"symbol": "$"' in data and "C:\\\\ws" not in data


def test_cmd_preflight_not_ready_exit_2(registry, capsys):
    code = cmd_preflight(None, registry=registry, finder=lambda: MAIN, probe=FakeProbe(foreground=99))
    printed = capsys.readouterr().out
    assert code == 2 and "[FAIL] in front" in printed and "NOT READY" in printed
