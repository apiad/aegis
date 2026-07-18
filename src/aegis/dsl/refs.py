from __future__ import annotations

import re
from typing import Any

_BRACE = re.compile(r"\{\{([^{}]*)\}\}")
_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*$")


class RefError(Exception):
    """A selector or template referenced something not present/resolvable."""


class Store:
    def __init__(self) -> None:
        self.outputs: dict[str, Any] = {}   # keyed by structural path
        self.refs: dict[str, Any] = {}      # keyed by node id

    def record(self, path: str, node_id: str | None, value: Any) -> None:
        self.outputs[path] = value
        if node_id:
            self.refs[node_id] = value

    def snapshot(self) -> dict:
        return {"outputs": dict(self.outputs), "refs": dict(self.refs)}

    def load(self, snap: dict) -> None:
        self.outputs = dict(snap.get("outputs", {}))
        self.refs = dict(snap.get("refs", {}))


def resolve_selector(selector: str, store: Store) -> Any:
    parts = selector.split(".")
    head, rest = parts[0], parts[1:]
    if head not in store.refs:
        raise RefError(f"unknown reference id: {head!r}")
    value: Any = store.refs[head]
    for seg in rest:
        value = _navigate(value, seg, selector)
    return value


def _navigate(value: Any, seg: str, selector: str) -> Any:
    if seg == "last":
        if not isinstance(value, list) or not value:
            raise RefError(f"{selector!r}: '.last' needs a non-empty list")
        return value[-1]
    if isinstance(value, dict):
        if seg not in value:
            raise RefError(f"{selector!r}: no key {seg!r}")
        return value[seg]
    if isinstance(value, list):
        if not seg.isdigit():
            raise RefError(f"{selector!r}: list index must be an integer, got {seg!r}")
        idx = int(seg)
        if idx >= len(value):
            raise RefError(f"{selector!r}: index {idx} out of range")
        return value[idx]
    raise RefError(f"{selector!r}: cannot navigate into {type(value).__name__}")


def substitute(template: str, bindings: dict) -> str:
    def _repl(m: re.Match) -> str:
        body = m.group(1)
        name_match = _NAME.match(body)
        if not name_match:
            raise RefError(
                f"unbound template name: {{{{{body}}}}} — only bare "
                "dotted names are allowed, no logic")
        name = name_match.group(1)
        cur: Any = bindings
        for seg in name.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                raise RefError(f"unbound template name: {{{{{name}}}}}")
        return str(cur)
    return _BRACE.sub(_repl, template)
