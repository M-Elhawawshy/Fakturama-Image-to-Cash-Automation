"""inspect/locate with a fake binder and the recorded tree (no Fakturama needed)."""

from pathlib import Path

from fakturama_agent.uia.binding import BindError, MainWindow
from fakturama_agent.uia.commands import cmd_inspect, cmd_locate
from fakturama_agent.uia.tree import Snapshot
from fakturama_agent.uia.winapi import WindowInfo

MAIN = Path(__file__).parent / "fixtures" / "uia" / "main_window.json"
FAKE_MAIN = MainWindow(hwnd=3, pid=100, title="Fakturama - C:\\ws", workspace="C:\\ws", minimized=False)


def finder():
    return FAKE_MAIN


def snapshotter(hwnd, **_kwargs):
    assert hwnd in (3, 77)
    return Snapshot.load(MAIN)


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


def test_locate_verified(capsys):
    assert cmd_locate([], finder=finder, snapshotter=snapshotter) == 0
    out = capsys.readouterr().out
    assert "toolbar.new_order" in out and "11/11 resolved" in out


def test_locate_unresolved_exit_2(capsys):
    assert cmd_locate(["toolbar.save", "order.cust_ref"], finder=finder, snapshotter=snapshotter) == 2
    out = capsys.readouterr().out
    assert "order.cust_ref  NOT_FOUND" in out
    assert "1/2 resolved" in out
