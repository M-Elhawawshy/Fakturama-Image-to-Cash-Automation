from pathlib import Path

import pytest
from pydantic import ValidationError

from fakturama_agent.uia.locators import LocatorError, Registry
from fakturama_agent.uia.tree import Snapshot, UiNode

MAIN = Path(__file__).parent / "fixtures" / "uia" / "main_window.json"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


@pytest.fixture(scope="module")
def main() -> Snapshot:
    return Snapshot.load(MAIN)


def _write(tmp_path, body: str) -> Path:
    path = tmp_path / "locators.yaml"
    path.write_text('version: 1\napp: test\nlocators:\n' + body, encoding="utf-8")
    return path


# --- the shipped registry against the recorded main window -------------------


def test_every_verified_locator_resolves_in_the_recorded_main_window(registry, main):
    # main-window locators; the open File menu has its own recording (main_file_menu.json)
    verified = [
        k for k, loc in registry.locators.items()
        if loc.status == "verified" and loc.window is None and not k.startswith("menu.file.")
    ]
    assert len(verified) == 11
    for key in verified:
        node = registry.resolve(main, key)
        assert node.control_type == registry.get(key).named.control_type


def test_toolbar_buttons_expose_invoke(registry, main):
    for key in ("toolbar.new_order", "toolbar.save", "toolbar.new_product", "toolbar.new_contact"):
        assert "Invoke" in registry.resolve(main, key).patterns


def test_order_locators_are_absent_without_an_order_editor(registry, main):
    with pytest.raises(LocatorError) as e:
        registry.resolve(main, "order.cust_ref")
    assert e.value.kind == "not_found"


def test_unknown_key_suggests_neighbours(registry, main):
    with pytest.raises(LocatorError) as e:
        registry.resolve(main, "toolbar.new_ordr")
    assert e.value.kind == "unknown_key"
    assert "toolbar.new_order" in e.value.detail


# --- resolution rules on small trees ----------------------------------------

TREE = {
    "control_type": "Window",
    "children": [
        {"control_type": "Button", "name": "Minimize"},
        {
            "control_type": "Pane",
            "name": "editor",
            "children": [
                {"control_type": "Text", "name": "Addresses"},
                {"control_type": "Image", "rect": [1, 1, 2, 2]},
                {"control_type": "Image", "rect": [3, 3, 4, 4]},
                {"control_type": "Button", "name": "Minimize"},
            ],
        },
    ],
}

RULES = """
  editor:
    named: {control_type: Pane, name: editor}
  select_icon:
    label_sibling: {label: Addresses, sibling_type: Image, nth: 0}
  plus_icon:
    label_sibling: {label: Addresses, sibling_type: Image, nth: 1}
  third_icon:
    label_sibling: {label: Addresses, sibling_type: Image, nth: 2}
  minimize:
    named: {control_type: Button, name: Minimize}
  editor_minimize:
    named: {control_type: Button, name: Minimize}
    within: editor
  min_regex:
    named: {control_type: Button, name_regex: "Mini.*"}
    within: editor
"""


@pytest.fixture
def rules(tmp_path) -> Registry:
    return Registry.load(_write(tmp_path, RULES))


def test_label_sibling_nth(rules):
    root = UiNode.from_dict(TREE)
    assert rules.resolve(root, "select_icon").rect == (1, 1, 2, 2)
    assert rules.resolve(root, "plus_icon").rect == (3, 3, 4, 4)
    with pytest.raises(LocatorError, match="not_found"):
        rules.resolve(root, "third_icon")


def test_ambiguous_and_within(rules):
    root = UiNode.from_dict(TREE)
    with pytest.raises(LocatorError) as e:
        rules.resolve(root, "minimize")
    assert e.value.kind == "ambiguous" and len(e.value.candidates) == 2
    assert rules.resolve(root, "editor_minimize").parent.name == "editor"
    assert rules.resolve(root, "min_regex").name == "Minimize"


def test_ambiguous_label(rules):
    tree = {"control_type": "Pane", "children": [{"control_type": "Text", "name": "Addresses"}] * 2}
    with pytest.raises(LocatorError) as e:
        rules.resolve(UiNode.from_dict(tree), "select_icon")
    assert e.value.kind == "ambiguous"


def test_truncated_snapshot_makes_absence_unreliable(rules):
    snap = Snapshot(root=UiNode.from_dict({"control_type": "Window"}), node_count=1, truncated=True)
    with pytest.raises(LocatorError) as e:
        rules.resolve(snap, "select_icon")
    assert e.value.kind == "unreliable"


@pytest.mark.parametrize(
    "body",
    [
        "  x:\n    named: {control_type: Button}\n",  # no name
        "  x:\n    named: {control_type: Button, name: a, name_regex: b}\n",  # both
        "  x:\n    status: verified\n",  # no query
        "  x:\n    named: {control_type: Button, name: a}\n    typo: 1\n",  # unknown field
    ],
)
def test_invalid_registry_is_rejected(tmp_path, body):
    with pytest.raises(ValidationError):
        Registry.load(_write(tmp_path, body))


def test_within_must_reference_a_known_locator(tmp_path):
    with pytest.raises(ValueError, match="within='nope'"):
        Registry.load(_write(tmp_path, "  x:\n    named: {control_type: Button, name: a}\n    within: nope\n"))


# --- dialog and menu locators against their recordings -----------------------


def _load(name: str) -> Snapshot:
    return Snapshot.load(MAIN.parent / f"{name}.json")


@pytest.mark.parametrize(
    "fixture, prefix",
    [("select_address", "selector."), ("main_file_menu", "menu.file."), ("prefs_general", "prefs.")],
)
def test_window_scoped_locators_resolve_in_their_recordings(registry, fixture, prefix):
    snap = _load(fixture)
    page_only = {"prefs.take_over_item_number"}  # on the Documents page
    for key in (k for k in registry.locators if k.startswith(prefix) and k not in page_only):
        registry.resolve(snap, key)


def test_search_edit_is_inside_a_pane_after_the_label(registry):
    edit = registry.resolve(_load("select_address"), "selector.search")
    assert edit.control_type == "Edit" and edit.parent.control_type == "Pane"


def test_take_over_checkbox_on_documents_page(registry):
    box = registry.resolve(_load("prefs_documents"), "prefs.take_over_item_number")
    assert box.toggle == "off" and "Toggle" in box.patterns


def test_preferences_menu_item_only_inside_the_open_file_menu(registry, main):
    assert "Invoke" in registry.resolve(_load("main_file_menu"), "menu.file.preferences").patterns
    with pytest.raises(LocatorError) as e:
        registry.resolve(main, "menu.file.preferences")
    assert (e.value.key, e.value.kind) == ("menu.file.expanded", "not_found")


def test_window_locators_declare_their_window(registry):
    for key, loc in registry.locators.items():
        if key.startswith(("selector.", "prefs.")):
            assert loc.window, key
        if key.startswith(("toolbar.", "menu.", "view.", "order.")):
            assert loc.window is None, key


def test_label_sibling_descend(tmp_path):
    reg = Registry.load(_write(tmp_path, "  s:\n    label_sibling: {label: 'Search:', sibling_type: Edit, descend: true}\n"
                                         "  flat:\n    label_sibling: {label: 'Search:', sibling_type: Edit}\n"))
    snap = _load("select_address")
    assert reg.resolve(snap, "s").control_type == "Edit"
    with pytest.raises(LocatorError, match="not_found"):
        reg.resolve(snap, "flat")


def test_within_across_windows_is_rejected(tmp_path):
    body = ("  a:\n    named: {control_type: Pane, name: a}\n    window: '^X$'\n"
            "  b:\n    named: {control_type: Button, name: b}\n    within: a\n")
    with pytest.raises(ValueError, match="another window"):
        Registry.load(_write(tmp_path, body))
