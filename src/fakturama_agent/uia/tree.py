"""UIA tree snapshots.

``UiNode`` is a plain, immutable-by-convention copy of a UIA subtree. The same
model is produced from the live application and loaded from recorded JSON
fixtures, so locator logic is tested offline against real recorded trees.

A live snapshot also keeps each node's live element (``element``) so the
caller can act on exactly what a locator resolved; it is not serialized.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Patterns worth recording: the ones the adapter acts through or reads.
PATTERNS = (
    "Invoke",
    "Value",
    "ExpandCollapse",
    "SelectionItem",
    "Selection",
    "Toggle",
    "Text",
    "Grid",
    "Table",
    "Scroll",
    "Window",
    "LegacyIAccessible",
)

DEFAULT_MAX_DEPTH = 40
DEFAULT_MAX_NODES = 6000


@dataclass(eq=False)
class UiNode:
    control_type: str
    name: str = ""
    automation_id: str = ""
    class_name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom (screen pixels)
    enabled: bool = True
    offscreen: bool = False
    value: str | None = None  # ValuePattern value, when exposed
    patterns: tuple[str, ...] = ()
    children: list[UiNode] = field(default_factory=list)
    parent: UiNode | None = field(default=None, repr=False)
    element: Any = field(default=None, repr=False)  # live pywinauto element info; None for fixtures

    # -- navigation ---------------------------------------------------------

    def walk(self) -> Iterator[UiNode]:
        """Depth-first, document order, including self."""
        yield self
        for child in self.children:
            yield from child.walk()

    def siblings_after(self) -> list[UiNode]:
        if self.parent is None:
            return []
        kids = self.parent.children
        return kids[kids.index(self) + 1 :]

    def path(self) -> str:
        parts = []
        node: UiNode | None = self
        while node is not None:
            parts.append(f"{node.control_type}{'[' + repr(node.name) + ']' if node.name else ''}")
            node = node.parent
        return " > ".join(reversed(parts))

    def label(self) -> str:
        return f"{self.control_type} {self.name!r}" if self.name else self.control_type

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2

    # -- (de)serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"control_type": self.control_type, "name": self.name}
        if self.automation_id:
            data["automation_id"] = self.automation_id
        if self.class_name:
            data["class_name"] = self.class_name
        data["rect"] = list(self.rect)
        if not self.enabled:
            data["enabled"] = False
        if self.offscreen:
            data["offscreen"] = True
        if self.value is not None:
            data["value"] = self.value
        if self.patterns:
            data["patterns"] = list(self.patterns)
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], parent: UiNode | None = None) -> UiNode:
        node = cls(
            control_type=data["control_type"],
            name=data.get("name", ""),
            automation_id=data.get("automation_id", ""),
            class_name=data.get("class_name", ""),
            rect=tuple(data.get("rect", (0, 0, 0, 0))),
            enabled=data.get("enabled", True),
            offscreen=data.get("offscreen", False),
            value=data.get("value"),
            patterns=tuple(data.get("patterns", ())),
            parent=parent,
        )
        node.children = [cls.from_dict(c, node) for c in data.get("children", ())]
        return node


@dataclass
class Snapshot:
    root: UiNode
    node_count: int
    truncated: bool  # hit max_depth or max_nodes; locators must not trust absence then
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        data = {"meta": self.meta, "node_count": self.node_count, "truncated": self.truncated, "root": self.root.to_dict()}
        return json.dumps(data, indent=1, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Snapshot:
        data = json.loads(text)
        return cls(
            root=UiNode.from_dict(data["root"]),
            node_count=data["node_count"],
            truncated=data["truncated"],
            meta=data.get("meta", {}),
        )

    @classmethod
    def load(cls, path: Path) -> Snapshot:
        return cls.from_json(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Live snapshots (pywinauto UIA backend)
# --------------------------------------------------------------------------


def redact(snapshot: Snapshot, secret: str, replacement: str = "<workspace>") -> None:
    """Replace a string (e.g. the workspace path) in every name/value, in place."""
    if not secret:
        return
    for node in snapshot.root.walk():
        node.name = node.name.replace(secret, replacement)
        if node.value is not None:
            node.value = node.value.replace(secret, replacement)
    snapshot.meta = {k: v.replace(secret, replacement) if isinstance(v, str) else v for k, v in snapshot.meta.items()}


def outline(node: UiNode, *, max_depth: int | None = None, _depth: int = 0) -> list[str]:
    """Indented one-line-per-node view for humans."""
    extras = []
    if node.value:
        extras.append(f"value={node.value!r}")
    if node.patterns:
        extras.append("[" + ",".join(p for p in node.patterns if p != "LegacyIAccessible") + "]")
    if not node.enabled:
        extras.append("disabled")
    lines = ["  " * _depth + node.label() + ("  " + " ".join(extras) if extras else "")]
    if max_depth is None or _depth < max_depth:
        for child in node.children:
            lines += outline(child, max_depth=max_depth, _depth=_depth + 1)
    return lines


def _pattern_property_ids() -> dict[str, int]:
    from pywinauto.uia_defines import IUIA

    dll = IUIA().UIA_dll
    return {p: getattr(dll, f"UIA_Is{p}PatternAvailablePropertyId") for p in PATTERNS}


def _node_from_element(info: Any, pattern_ids: dict[str, int], parent: UiNode | None) -> UiNode:
    from pywinauto.uia_defines import IUIA

    dll = IUIA().UIA_dll
    element = info.element
    patterns = tuple(p for p, pid in pattern_ids.items() if _safe(lambda: element.GetCurrentPropertyValue(pid), False))
    value = None
    if "Value" in patterns:
        value = _safe(lambda: element.GetCurrentPropertyValue(dll.UIA_ValueValuePropertyId), None)
    r = _safe(lambda: info.rectangle, None)
    rect = (r.left, r.top, r.right, r.bottom) if r is not None else (0, 0, 0, 0)
    return UiNode(
        control_type=_safe(lambda: info.control_type, "") or "",
        name=_safe(lambda: info.name, "") or "",
        automation_id=_safe(lambda: info.automation_id, "") or "",
        class_name=_safe(lambda: info.class_name, "") or "",
        rect=rect,
        enabled=bool(_safe(lambda: info.enabled, True)),
        offscreen=bool(_safe(lambda: element.GetCurrentPropertyValue(dll.UIA_IsOffscreenPropertyId), False)),
        value=value if isinstance(value, str) else None,
        patterns=patterns,
        parent=parent,
        element=info,
    )


def _safe(fn, default):
    """UIA calls on SWT can raise COMError for vanished or half-built elements."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - any COM failure means "not available"
        return default


def snapshot_element(
    info: Any, *, max_depth: int = DEFAULT_MAX_DEPTH, max_nodes: int = DEFAULT_MAX_NODES, meta: dict | None = None
) -> Snapshot:
    """Copy the live subtree under a pywinauto UIAElementInfo."""
    pattern_ids = _pattern_property_ids()
    count = 0
    truncated = False

    def build(element_info: Any, parent: UiNode | None, depth: int) -> UiNode:
        nonlocal count, truncated
        count += 1
        node = _node_from_element(element_info, pattern_ids, parent)
        if depth >= max_depth:
            truncated = truncated or bool(_safe(lambda: element_info.children(), []))
            return node
        for child in _safe(lambda: element_info.children(), []):
            if count >= max_nodes:
                truncated = True
                break
            node.children.append(build(child, node, depth + 1))
        return node

    root = build(info, None, 0)
    return Snapshot(root=root, node_count=count, truncated=truncated, meta=dict(meta or {}))


def snapshot_hwnd(hwnd: int, **kwargs: Any) -> Snapshot:
    from pywinauto.uia_element_info import UIAElementInfo

    return snapshot_element(UIAElementInfo(hwnd), **kwargs)
