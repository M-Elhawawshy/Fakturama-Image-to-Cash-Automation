from pathlib import Path

from fakturama_agent.uia.tree import Snapshot, UiNode, outline, redact

MAIN = Path(__file__).parent / "fixtures" / "uia" / "main_window.json"


def test_fixture_round_trip():
    text = MAIN.read_text(encoding="utf-8")
    snap = Snapshot.from_json(text)
    assert snap.to_json() == text
    assert sum(1 for _ in snap.root.walk()) == snap.node_count
    assert snap.truncated is False
    assert snap.root.control_type == "Window"
    assert snap.root.name == "Fakturama - <workspace>"


def test_parent_links_and_paths():
    snap = Snapshot.load(MAIN)
    for node in snap.root.walk():
        for child in node.children:
            assert child.parent is node
    button = next(n for n in snap.root.walk() if n.name == "Create: New Order")
    assert button.path().startswith("Window['Fakturama - <workspace>'] > ")
    assert button.path().endswith("Button['Create: New Order']")
    assert button.center == ((button.rect[0] + button.rect[2]) // 2, (button.rect[1] + button.rect[3]) // 2)


def _tree() -> UiNode:
    return UiNode.from_dict(
        {
            "control_type": "Pane",
            "children": [
                {"control_type": "Text", "name": "Addresses"},
                {"control_type": "Image"},
                {"control_type": "Image"},
                {"control_type": "Edit", "name": "secret C:\\ws", "value": "C:\\ws\\x", "patterns": ["Value"]},
            ],
        }
    )


def test_siblings_after():
    root = _tree()
    label = root.children[0]
    assert [n.control_type for n in label.siblings_after()] == ["Image", "Image", "Edit"]
    assert root.siblings_after() == []


def test_outline_and_redact():
    snap = Snapshot(root=_tree(), node_count=5, truncated=False, meta={"window_title": "Fakturama - C:\\ws"})
    redact(snap, "C:\\ws")
    edit = snap.root.children[3]
    assert (edit.name, edit.value) == ("secret <workspace>", "<workspace>\\x")
    assert snap.meta["window_title"] == "Fakturama - <workspace>"
    assert outline(snap.root) == [
        "Pane",
        "  Text 'Addresses'",
        "  Image",
        "  Image",
        "  Edit 'secret <workspace>'  value='<workspace>\\\\x' [Value]",
    ]


def test_redact_covers_url_and_slash_forms():
    ws = r"C:\Users\me\My Data"
    root = UiNode.from_dict({"control_type": "Pane", "children": [
        {"control_type": "Edit", "value": "file:///C:/Users/me/My%20Data/Templates/start.html"},
        {"control_type": "Edit", "value": "C:/Users/me/My Data/x"},
    ]})
    snap = Snapshot(root=root, node_count=3, truncated=False)
    redact(snap, ws)
    assert [c.value for c in root.children] == ["file:///<workspace>/Templates/start.html", "<workspace>/x"]
