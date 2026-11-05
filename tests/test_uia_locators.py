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
    verified = [k for k, loc in registry.locators.items() if loc.status == "verified"]
    assert len(verified) >= 11
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
