"""A plane that takes a state_dir must be handed one in every brain path.

QueueManager and InboxRouter both degrade to memory-only when state_dir
is None, and both brain paths constructed them that way for months. The
queue's whole persistence and replay layer never ran, and the eight tests
covering it passed because every one passed state_dir by hand.

This walks the AST of the brain-boot modules instead of trusting a
call site, because a call site is what went wrong.
"""
from __future__ import annotations

import ast
from pathlib import Path

import aegis
from aegis.core.planes import STATEFUL_PLANES

SRC = Path(aegis.__file__).parent


def _calls_to(module_path: str, ctor: str) -> list[ast.Call]:
    tree = ast.parse((SRC / module_path).read_text(encoding="utf-8"))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) == ctor
             or getattr(n.func, "attr", None) == ctor)
    ]


def test_every_stateful_plane_is_constructed_with_a_state_dir():
    offenders = []
    for ctor, module_path in STATEFUL_PLANES:
        calls = _calls_to(module_path, ctor)
        assert calls, f"no {ctor}(...) call found in {module_path}"
        for call in calls:
            if not any(kw.arg == "state_dir" for kw in call.keywords):
                offenders.append(f"{module_path}:{call.lineno} {ctor}(...)")
    assert not offenders, (
        "stateful planes constructed without state_dir:\n  "
        + "\n  ".join(offenders)
        + "\nA plane built without it silently persists nothing, and its "
          "replay never runs."
    )
