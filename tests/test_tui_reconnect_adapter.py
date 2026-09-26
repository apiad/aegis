"""The queue's session manager in standalone TUI mode is a facade, and the
recovery plane called a method it did not have.

Under `aegis serve` the queue talks to a real SessionManager and
stall-and-rebuild works. In standalone TUI mode the app is its own bridge
and the queue talks to `_SessionManagerAdapter`, which had no `reconnect`
and no `get`. So `recovery.rebuild` raised AttributeError, `rebuild`'s own
`except Exception` — there so a finalizer never strands a task — swallowed
it, rebuild returned False, and the worker parked on the FIRST stall
without one rebuild ever being attempted. Every test passed, because the
rig stubs the session manager.

Two tests, because they fail for different reasons. The behavioural one
drives the real adapter through `recovery.rebuild` and would catch a
`reconnect` that is present but broken. The structural one reads the
method names out of the recovery plane's own source, so a rebuild step
that starts calling `sm.something_new` fails here rather than in a daemon.

The structural half covers `restore` as well as `rebuild`, and reads what
`restore` needs off its own call node — the seam it resolves is named by a
string literal, and its keyword list grows.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from aegis.core import recovery
from aegis.core.manager import SessionManager
from aegis.tui.app import _SessionManagerAdapter


def _recovery_func(func_name: str) -> ast.AST:
    tree = ast.parse(inspect.getsource(recovery))
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        and n.name == func_name
    )


def _sm_attrs(func_name: str) -> set[str]:
    """Every `sm.<attr>` the named function in `core.recovery` REQUIRES.

    Dotted access and `getattr(sm, "name")` both count. A three-argument
    `getattr(sm, "name", fallback)` does not: the fallback is what makes
    that name optional, and the seam it opens is pinned by
    `_restore_seam` instead.
    """
    fn = _recovery_func(func_name)
    attrs = {
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "sm"
    }
    attrs |= {
        c.args[1].value
        for c in ast.walk(fn)
        if _is_sm_getattr(c) and len(c.args) == 2
    }
    return attrs


def _is_sm_getattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "sm"
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    )


def _restore_seam() -> tuple[str, str, set[str]]:
    """What `restore` cold-rebuilds a worker through: the preferred seam
    name, the fallback attribute, and every keyword it passes.

    All three read off `restore`'s own call node rather than being written
    down here. The preferred name is a STRING LITERAL — `getattr(sm,
    "_sync_spawn", sm.spawn)` — so no walk over dotted attributes can see
    it, and a keyword list copied into a test goes stale the day a fourth
    one is added: the daemon would be where that failed, not CI.
    """
    fn = _recovery_func("restore")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not _is_sm_getattr(node.value):
            continue
        seam_call = node.value
        if len(seam_call.args) != 3:
            continue
        fallback = seam_call.args[2]
        assert isinstance(fallback, ast.Attribute), (
            "restore's spawn seam no longer falls back to an sm attribute"
        )
        bound = node.targets[0].id
        made = next(
            c
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == bound
        )
        return (
            seam_call.args[1].value,
            fallback.attr,
            {kw.arg for kw in made.keywords if kw.arg},
        )
    raise AssertionError("restore no longer resolves a spawn seam off sm")


def test_the_adapter_exposes_what_rebuild_calls():
    missing = sorted(a for a in _sm_attrs("rebuild")
                     if not hasattr(_SessionManagerAdapter, a))
    assert not missing, (
        f"recovery.rebuild calls sm.{{{','.join(missing)}}}, which "
        "_SessionManagerAdapter does not have — rebuild would raise "
        "AttributeError, swallow it, and park on the first stall"
    )


def test_the_adapter_reconnect_takes_allow_local():
    """`rebuild` passes it by keyword, and a positional-only or absent
    parameter is a TypeError swallowed exactly like a missing method."""
    sig = inspect.signature(_SessionManagerAdapter.reconnect)
    p = sig.parameters.get("allow_local")
    assert p is not None and p.kind is inspect.Parameter.KEYWORD_ONLY


async def test_rebuild_against_the_real_adapter_succeeds(pane_app):
    """The end-to-end shape: a local pane with a conversation id, rebuilt
    through the adapter the way a stalled queue worker is."""
    async with pane_app() as (pane, pilot):
        app = pilot.app
        core = pane._core
        core._session.session_id = "sess-1"
        delivered = []
        core.add_inbox_observer(lambda _s, msg: delivered.append(msg))

        ok = await recovery.rebuild(
            _SessionManagerAdapter(app), pane.handle, nudge="carry on"
        )

        assert ok is True
        assert [m.body for m in delivered] == ["carry on"]


async def test_rebuild_reports_failure_when_there_is_nothing_to_resume(pane_app):
    """The other arm: no session id means no conversation to rebuild, and
    rebuild must say so rather than claim success."""
    async with pane_app() as (pane, pilot):
        assert await recovery.rebuild(
            _SessionManagerAdapter(pilot.app), pane.handle, nudge="carry on"
        ) is False


# ---------------------------------------------------------------------------
# restore: the cold-rebuild half, which has its own seam and its own kwargs
# ---------------------------------------------------------------------------


def test_the_adapter_exposes_what_restore_calls():
    """`rebuild` is only half of what the recovery plane asks a session
    manager for. `restore` is the other half — the branch that runs when
    the front end did NOT put the worker's pane back — and it reaches for
    different attributes."""
    missing = sorted(a for a in _sm_attrs("restore")
                     if not hasattr(_SessionManagerAdapter, a))
    assert not missing, (
        f"recovery.restore calls sm.{{{','.join(missing)}}}, which "
        "_SessionManagerAdapter does not have — restore would raise, be "
        "swallowed into None, and every unrestored task would park"
    )


@pytest.mark.parametrize("cls", [_SessionManagerAdapter, SessionManager])
def test_the_restore_spawn_seam_is_satisfied_by_both_session_managers(cls):
    """Whichever of the two names `restore` resolves has to take every
    keyword it passes — on BOTH classes that can be the session manager.

    Derived from the call node, so a fifth keyword added to `restore`
    tomorrow fails here rather than in a daemon: a spawn seam that is
    missing one raises TypeError inside `restore`'s own `except`, which
    parks the task and says nothing about why.

    Parametrised because checking the adapter alone checked the branch
    production never takes. The adapter has no `_sync_spawn`, so
    `getattr(sm, "_sync_spawn", sm.spawn)` falls back to `spawn` and this
    read the facade. Under `aegis serve` — every production daemon — it
    resolves to the real `SessionManager._sync_spawn`, which was never
    looked at: rename `cwd` to `workdir` there and every in-flight task
    parks with "could not rebuild after the restart" while the suite stays
    green.
    """
    seam, fallback, passed = _restore_seam()
    resolved = getattr(cls, seam, None)
    used = seam
    if resolved is None:
        resolved, used = getattr(cls, fallback, None), fallback
    assert resolved is not None, (
        f"{cls.__name__} has neither {seam!r} nor {fallback!r}"
    )
    params = inspect.signature(resolved).parameters
    missing = sorted(k for k in passed if k not in params)
    assert not missing, (
        f"recovery.restore passes {missing} to {cls.__name__}.{used}, which "
        "does not take them — the cold rebuild is a TypeError, swallowed "
        "into a park"
    )
