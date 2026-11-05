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
        if loc.status == "verified":
            registry.resolve(snap, key)
