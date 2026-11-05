"""Read File > Preferences pages and close the dialog with Cancel (changes nothing).

Live sequence (recorded 2026-11-05): Expand the File MenuItem (it exposes no
Invoke), Invoke its Preferences item, wait for the top-level "Preferences"
dialog, select each tree page and wait for its controls, snapshot it, and
finally Invoke Cancel and wait until the dialog is gone. Nothing is typed,
toggled or applied.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from fakturama_agent.uia import actions
from fakturama_agent.uia.binding import MainWindow, process_windows
from fakturama_agent.uia.locators import LocatorError, Registry
from fakturama_agent.uia.tree import Snapshot, snapshot_hwnd

# page tree item -> a control that exists only once that page is shown
PAGES = {
    "prefs.page.general": "prefs.currency_example",
    "prefs.page.documents": "prefs.take_over_item_number",
}


class PreferencesError(RuntimeError):
    pass


def _dialog_hwnd(pid: int, title_regex: str, lister: Callable[[int], list]) -> int | None:
    pattern = re.compile(title_regex)
    found = [w for w in lister(pid) if pattern.search(w.title)]
    if len(found) > 1:
        raise PreferencesError(f"{len(found)} windows match {title_regex!r}")
    return found[0].hwnd if found else None


def read_preferences(
    main: MainWindow,
    registry: Registry,
    *,
    timeout: float = 10.0,
    lister: Callable[[int], list] = process_windows,
    snapshotter: Callable[..., Snapshot] = snapshot_hwnd,
    on_menu_open: Callable[[Snapshot], None] | None = None,
) -> dict[str, Snapshot]:
    """Snapshots of the Preferences pages in ``PAGES``, keyed by page locator. Always closes with Cancel."""
    window_regex = registry.get("prefs.cancel").window
    assert window_regex is not None
    if _dialog_hwnd(main.pid, window_regex, lister) is not None:
        raise PreferencesError("a Preferences dialog is already open; close it with Cancel first")

    file_menu = registry.resolve(snapshotter(main.hwnd), "menu.file")
    actions.open_menu(file_menu, main.hwnd)
    try:

        def menu_open() -> Snapshot:
            snap = snapshotter(main.hwnd)
            registry.resolve(snap, "menu.file.preferences")
            return snap

        menu_snap = actions.wait_until(menu_open, timeout=timeout, what="File > Preferences menu item")
        if on_menu_open is not None:
            on_menu_open(menu_snap)
        actions.invoke(registry.resolve(menu_snap, "menu.file.preferences"))
    except Exception:
        actions.press_escape(main.hwnd)
        raise

    hwnd = actions.wait_until(
        lambda: _dialog_hwnd(main.pid, window_regex, lister), timeout=timeout, what="the Preferences dialog"
    )
    try:
        pages: dict[str, Snapshot] = {}
        for page_key, marker_key in PAGES.items():
            actions.select(registry.resolve(snapshotter(hwnd), page_key))

            def shown(marker_key: str = marker_key) -> Snapshot | None:
                snap = snapshotter(hwnd)
                try:
                    registry.resolve(snap, marker_key)
                except LocatorError:
                    return None
                return snap

            pages[page_key] = actions.wait_until(shown, timeout=timeout, what=f"Preferences page for {page_key}")
        return pages
    finally:
        _cancel(hwnd, main.pid, window_regex, registry, timeout, lister, snapshotter)


def _cancel(hwnd, pid, window_regex, registry, timeout, lister, snapshotter) -> None:
    try:
        actions.invoke(registry.resolve(snapshotter(hwnd), "prefs.cancel"))
        actions.wait_until(
            lambda: True if _dialog_hwnd(pid, window_regex, lister) is None else None,
            timeout=timeout,
            what="the Preferences dialog to close",
        )
    except Exception as e:
        raise PreferencesError(f"could not close Preferences with Cancel ({e}); close it by hand with Cancel") from e
