"""Locator registry (locators.yaml) and resolution on a UIA snapshot.

Resolution is strict: a locator must match exactly one node in its scope.
Zero matches is "not found", several is "ambiguous"; in a truncated snapshot
zero matches is "unreliable", because absence cannot be trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from fakturama_agent.uia.tree import Snapshot, UiNode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NamedQuery(_Strict):
    control_type: str
    name: str | None = None
    name_regex: str | None = None

    @model_validator(mode="after")
    def _one_name(self) -> NamedQuery:
        if (self.name is None) == (self.name_regex is None):
            raise ValueError("named query needs exactly one of name / name_regex")
        return self


class LabelSiblingQuery(_Strict):
    label: str
    label_type: str = "Text"
    sibling_type: str
    nth: int = 0
    descend: bool = False  # also search inside the following siblings (document order)


class Locator(_Strict):
    named: NamedQuery | None = None
    label_sibling: LabelSiblingQuery | None = None
    within: str | None = None
    window: str | None = None  # title regex of the top-level dialog this lives in; None = main window
    transient: bool = False  # present only while a menu/view is open; `locate` skips it when absent
    status: Literal["verified", "unverified"] = "unverified"
    note: str | None = None

    @model_validator(mode="after")
    def _one_query(self) -> Locator:
        if (self.named is None) == (self.label_sibling is None):
            raise ValueError("locator needs exactly one of named / label_sibling")
        return self


class LocatorFile(_Strict):
    version: Literal[1]
    app: str
    locators: dict[str, Locator]


class LocatorError(LookupError):
    def __init__(self, key: str, kind: str, detail: str, candidates: list[UiNode] | None = None) -> None:
        super().__init__(f"{key}: {kind} ({detail})")
        self.key = key
        self.kind = kind  # unknown_key | not_found | ambiguous | unreliable
        self.detail = detail
        self.candidates = candidates or []


@dataclass(frozen=True)
class Registry:
    app: str
    locators: dict[str, Locator]

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        if path is None:
            text = resources.files("fakturama_agent.uia").joinpath("locators.yaml").read_text(encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        parsed = LocatorFile.model_validate(yaml.safe_load(text))
        registry = cls(app=parsed.app, locators=dict(parsed.locators))
        for key, loc in registry.locators.items():
            if loc.within is not None and loc.within not in registry.locators:
                raise ValueError(f"locator {key!r}: within={loc.within!r} is not a known locator")
            if loc.within is not None and registry.locators[loc.within].window != loc.window:
                raise ValueError(f"locator {key!r}: within={loc.within!r} lives in another window")
            if loc.window is not None:
                re.compile(loc.window)
        return registry

    def get(self, key: str) -> Locator:
        try:
            return self.locators[key]
        except KeyError:
            prefix = key.split(".")[0]
            near = sorted(k for k in self.locators if k.startswith(prefix))
            raise LocatorError(key, "unknown_key", f"not in locators.yaml; same prefix: {near}") from None

    # -- resolution ---------------------------------------------------------

    def resolve(self, snapshot: Snapshot | UiNode, key: str) -> UiNode:
        root, truncated = (snapshot.root, snapshot.truncated) if isinstance(snapshot, Snapshot) else (snapshot, False)
        loc = self.get(key)
        scope = self.resolve(Snapshot(root, 0, truncated), loc.within) if loc.within else root
        if loc.named is not None:
            matches = _named(scope, loc.named)
        else:
            matches = _label_sibling(scope, loc.label_sibling, key)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            kind = "unreliable" if truncated else "not_found"
            raise LocatorError(key, kind, f"no match for {_describe(loc)} under {scope.label()}")
        raise LocatorError(key, "ambiguous", f"{len(matches)} matches for {_describe(loc)}", matches)


def _describe(loc: Locator) -> str:
    if loc.named is not None:
        q = loc.named
        return f"{q.control_type} {q.name!r}" if q.name is not None else f"{q.control_type} ~/{q.name_regex}/"
    q = loc.label_sibling
    inside = " (incl. descendants)" if q.descend else ""
    return f"{q.sibling_type} #{q.nth} after {q.label_type} {q.label!r}{inside}"


def _named(scope: UiNode, q: NamedQuery) -> list[UiNode]:
    pattern = re.compile(q.name_regex) if q.name_regex is not None else None
    return [
        n
        for n in scope.walk()
        if n.control_type == q.control_type
        and (n.name == q.name if pattern is None else pattern.fullmatch(n.name) is not None)
    ]


def _label_sibling(scope: UiNode, q: LabelSiblingQuery, key: str) -> list[UiNode]:
    labels = [n for n in scope.walk() if n.control_type == q.label_type and n.name == q.label]
    if len(labels) > 1:
        raise LocatorError(key, "ambiguous", f"{len(labels)} {q.label_type} labels named {q.label!r}", labels)
    if not labels:
        return []
    following = labels[0].siblings_after()
    if q.descend:
        following = [n for sib in following for n in sib.walk()]
    siblings = [s for s in following if s.control_type == q.sibling_type]
    return [siblings[q.nth]] if q.nth < len(siblings) else []
