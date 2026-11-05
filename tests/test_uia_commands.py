"""inspect/locate with a fake binder and the recorded tree (no Fakturama needed)."""

from pathlib import Path

from fakturama_agent.uia.binding import BindError, MainWindow
from fakturama_agent.uia.commands import cmd_inspect, cmd_locate
from fakturama_agent.uia.tree import Snapshot
from fakturama_agent.uia.winapi import WindowInfo

FIX = Path(__file__).parent / "fixtures" / "uia"
MAIN = FIX / "main_window.json"
FAKE_MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama - C:\\ws", workspace="C:\\ws", minimized=False)


def finder():
    return FAKE_MAIN


def snapshotter(hwnd, **_kwargs):
    assert hwnd in (3, 77, 88)
    return Snapshot.load({3: MAIN, 77: MAIN, 88: FIX / "select_address.json"}[hwnd])


def no_dialogs(pid):
    return [WindowInfo(3, pid, "SWT_Window0", FAKE_MAIN.title, True, False, "Fakturama.exe")]


def address_dialog_open(pid):
    return [*no_dialogs(pid), WindowInfo(88, pid, "SWT_Window0", "Select the address", True, False, "Fakturama.exe")]


def no_fakturama():
    raise BindError("Fakturama is not running; start it and open the workspace first")


def test_inspect_writes_snapshot_and_outline(tmp_path, capsys):
    out = tmp_path / "tree.json"
    assert cmd_inspect(out, finder=finder, snapshotter=snapshotter) == 0
    printed = capsys.readouterr().out
    assert "Button 'Create: New Order'  [Invoke]" in printed
    snap = Snapshot.load(out)
    assert snap.meta["pid"] == 100
    assert snap.node_count == Snapshot.load(MAIN).node_count


def test_inspect_dialog_by_title(tmp_path, capsys):
    def lister(pid):
        return [WindowInfo(77, pid, "SWT_Window0", "Select the address", True, False, "Fakturama.exe")]

    assert cmd_inspect(None, window_regex="^Select the", finder=finder, snapshotter=snapshotter,
                       window_lister=lister, show_outline=False) == 0
    assert cmd_inspect(None, window_regex="^Nope", finder=finder, snapshotter=snapshotter,
                       window_lister=lister, show_outline=False) == 1
    assert "found 0" in capsys.readouterr().err


def test_inspect_without_fakturama(capsys):
    assert cmd_inspect(None, finder=no_fakturama, snapshotter=snapshotter) == 1
    assert "not running" in capsys.readouterr().err


def test_locate_verified_skips_closed_dialogs_and_menus(capsys):
    assert cmd_locate([], finder=finder, snapshotter=snapshotter, window_lister=no_dialogs) == 0
    out = capsys.readouterr().out
    assert "toolbar.new_order" in out and "12/25 resolved, 0 failed, 13 skipped" in out
    lines = {line.split()[0]: " ".join(line.split()[1:]) for line in out.splitlines() if line.strip()}
    assert lines["menu.file.preferences"] == "SKIPPED menu.file.expanded not open"
    assert lines["menu.file.expanded"] == "SKIPPED not open"
    assert lines["prefs.cancel"] == "SKIPPED window '^Preferences$' not open"


def test_locate_resolves_in_an_open_dialog(capsys):
    keys = ["selector.search", "selector.ok", "selector.cancel"]
    assert cmd_locate(keys, finder=finder, snapshotter=snapshotter, window_lister=address_dialog_open) == 0
    assert "3/3 resolved" in capsys.readouterr().out


def test_locate_unresolved_exit_2(capsys):
    assert cmd_locate(["toolbar.save", "order.cust_ref"], finder=finder, snapshotter=snapshotter,
                      window_lister=no_dialogs) == 2
    out = capsys.readouterr().out
    assert "order.cust_ref  NOT_FOUND" in out
    assert "1/2 resolved, 1 failed" in out
