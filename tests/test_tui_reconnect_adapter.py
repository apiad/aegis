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
"""
from __future__ import annotations

import ast
import inspect

from aegis.core import recovery
from aegis.tui.app import _SessionManagerAdapter


def _sm_attrs(func_name: str) -> set[str]:
    """Every `sm.<attr>` the named function in `core.recovery` touches."""
    tree = ast.parse(inspect.getsource(recovery))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        and n.name == func_name
    )
    return {
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "sm"
    }


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
