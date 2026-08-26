"""/recap, and the four-surface rule."""
import inspect

import pytest

from aegis.commands import CommandContext, dispatch
from aegis.recap import Recap


class Bridge:
    def __init__(self, recap):
        self._recap = recap
        self.calls = []

    async def recap(self, handle, *, session_scope=True):
        self.calls.append((handle, session_scope))
        return self._recap


@pytest.mark.asyncio
async def test_recap_command_renders_the_block():
    bridge = Bridge(Recap(building="the judge", done="the spec",
                          remaining="the wiring", ok=True, model="haiku"))
    res = await dispatch("/recap", CommandContext(bridge, "agent-1"))
    assert res.ok is True
    assert "the judge" in res.title or "the judge" in res.body
    assert res.effect["kind"] == "recap"
    # A plain dict, because the web seam ships `effect` straight out as
    # JSON — the trap /btw already documented.
    assert isinstance(res.effect["recap"], dict)


@pytest.mark.asyncio
async def test_recap_command_asks_for_the_session_scope():
    """The automatic one-liner is turn-scoped; /recap is not."""
    bridge = Bridge(Recap(building="x", ok=True))
    await dispatch("/recap", CommandContext(bridge, "agent-1"))
    assert bridge.calls == [("agent-1", True)]


@pytest.mark.asyncio
async def test_a_failed_recap_is_an_error_result():
    bridge = Bridge(Recap(error="no transcript"))
    res = await dispatch("/recap", CommandContext(bridge, "agent-1"))
    assert res.ok is False
    assert "no transcript" in res.body


def test_recap_is_deferred_like_btw():
    """~7s measured. Awaiting it in a frontend's input handler would hold
    the pane's message pump for all of it."""
    from aegis.commands import REGISTRY
    import aegis.commands.builtins  # noqa: F401 — force registration
    assert REGISTRY["recap"].deferred is True


def test_every_bridge_takes_the_same_recap_signature():
    """A signature that drifts on one bridge breaks that frontend and no
    other — the hardest kind of bug to see. Same guard read_peer carries."""
    from aegis.core.manager import SessionManager
    from aegis.mcp.bridge import AppBridge
    from aegis.tui.app import AegisApp
    from aegis.tui.remote_manager import RemoteSessionManager

    want = inspect.signature(AppBridge.recap)
    for impl in (SessionManager, AegisApp, RemoteSessionManager):
        assert inspect.signature(impl.recap) == want, impl.__name__
