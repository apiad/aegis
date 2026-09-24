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
            where = f"{module_path}:{call.lineno} {ctor}(...)"
            passed = next((kw for kw in call.keywords if kw.arg == "state_dir"), None)
            if passed is None:
                offenders.append(f"{where} has no state_dir keyword")
            elif isinstance(passed.value, ast.Constant) and passed.value.value is None:
                offenders.append(f"{where} passes state_dir, but its value is None")
    assert not offenders, (
        "stateful planes constructed without a state directory:\n  "
        + "\n  ".join(offenders)
        + "\nA plane built without it silently persists nothing, and its "
          "replay never runs."
    )
