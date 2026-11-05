"""Read-only checks against the running Fakturama. Run with: python -m uv run pytest -m uia"""

import pytest

from fakturama_agent.uia.binding import find_main_window
from fakturama_agent.uia.locators import Registry
from fakturama_agent.uia.tree import snapshot_hwnd

pytestmark = pytest.mark.uia


def test_bind_and_resolve_verified_locators():
    main = find_main_window()
    assert not main.minimized, "restore the Fakturama window first"
    snap = snapshot_hwnd(main.hwnd)
    assert not snap.truncated
    registry = Registry.load()
    for key, loc in registry.locators.items():
        always_there = loc.window is None and not loc.transient and loc.within is None
        if loc.status == "verified" and always_there:
            registry.resolve(snap, key)


def test_preflight_live():
    """Opens File > Preferences, reads it, closes it with Cancel, brings Fakturama to the front."""
    from fakturama_agent.uia.preflight import LiveProbe, run_preflight

    registry = Registry.load()
    main = find_main_window()
    report = run_preflight(main, LiveProbe(registry), registry)
    by_name = {c.name: c for c in report.checks}
    assert by_name["version"].status == "ok"
    assert by_name["English UI"].status == "ok"
    assert by_name["currency format"].status == "ok", by_name["currency format"].seen
    assert report.money is not None
    # the dialog was closed with Cancel
    from fakturama_agent.uia.binding import process_windows

    assert not [w for w in process_windows(main.pid) if w.title == "Preferences"]
