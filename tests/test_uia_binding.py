import pytest

from fakturama_agent.uia.binding import BindError, find_main_window, process_windows
from fakturama_agent.uia.winapi import WindowInfo

WS = r"C:\Users\someone\Fakturama data"


def win(hwnd, title, *, cls="SWT_Window0", visible=True, pid=100, exe="Fakturama.exe", minimized=False):
    return WindowInfo(hwnd=hwnd, pid=pid, class_name=cls, title=title, visible=visible, minimized=minimized, exe_name=exe)


# Shape of the real window list seen on 2026-11-05: one visible main window plus hidden SWT helpers.
REAL = [
    win(1, "", cls="ComboLBox", visible=False),
    win(2, "", cls="#32770", visible=False),
    win(3, f"Fakturama - {WS}"),
    win(4, "SWT_Window_Fakturama", visible=False),
    win(5, "PartRenderingEngine's limbo", visible=False),
    win(6, "", cls="Internet Explorer_Hidden"),
    win(7, "Fakturama - Google Chrome", cls="Chrome_WidgetWin_1", pid=200, exe="chrome.exe"),
]


def test_binds_the_visible_main_window_among_helpers():
    main = find_main_window(REAL)
    assert (main.hwnd, main.pid, main.workspace, main.minimized) == (3, 100, WS, False)


def test_title_without_workspace():
    assert find_main_window([win(9, "Fakturama")]).workspace is None


def test_not_running():
    with pytest.raises(BindError, match="not running"):
        find_main_window([REAL[-1]])


def test_running_without_visible_main_window():
    hidden = [w for w in REAL if w.hwnd != 3]
    with pytest.raises(BindError, match=r"running \(pid \[100\]\) but has no visible main window"):
        find_main_window(hidden)


def test_two_instances_are_refused():
    with pytest.raises(BindError, match="2 Fakturama main windows"):
        find_main_window([*REAL, win(30, "Fakturama - D:\\other", pid=300)])


@pytest.mark.parametrize(
    "imposter",
    [
        win(40, "Fakturama - notes.txt", cls="Notepad", exe="notepad.exe", pid=400),  # wrong class/exe
        win(41, "Fakturama - X", exe="javaw.exe", pid=401),  # right class, wrong process
        win(42, "Fakturama Help", pid=100),  # right process, wrong title
    ],
)
def test_lookalike_windows_are_ignored(imposter):
    assert find_main_window([*REAL, imposter]).hwnd == 3


def test_minimized_is_reported_not_rejected():
    assert find_main_window([win(3, "Fakturama", minimized=True)]).minimized is True


def test_process_windows():
    assert [w.hwnd for w in process_windows(100, REAL)] == [3, 6]
    assert len(process_windows(100, REAL, visible_only=False)) == 6
