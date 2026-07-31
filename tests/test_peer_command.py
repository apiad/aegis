"""`/peer` — the command surface over `AppBridge.peer_ask`."""
from __future__ import annotations

import pytest

from aegis.commands import CommandContext, dispatch
from aegis.peer import PeerAnswer


class FakeInfo:
    def __init__(self, handle, slug="claude", state="ready"):
        self.handle, self.agent_slug, self.state = handle, slug, state
        self.active = False
        self.unseen = False


class FakeBridge:
    def __init__(self, answer=None, sessions=()):
        self.answer = answer or PeerAnswer(
            answer="the build is green", target="beta", ok=True,
            model="opus", duration_ms=8200, cost_usd=0.0410)
        self.calls: list = []
        self._sessions = list(sessions)

    def list_sessions(self):
        return self._sessions

    async def peer_ask(self, from_handle, target, prompt, *, cc=False):
        self.calls.append((from_handle, target, prompt, cc))
        return self.answer


async def run(text, bridge=None, handle="alpha"):
    b = bridge or FakeBridge()
    return await dispatch(text, CommandContext(bridge=b, handle=handle)), b


@pytest.mark.asyncio
async def test_peer_passes_source_target_and_question_through():
    res, b = await run("/peer beta is the build green?")
    assert res.ok
    assert b.calls == [("alpha", "beta", "is the build green?", False)]


@pytest.mark.asyncio
async def test_the_answer_rides_the_effect_channel_as_a_plain_dict():
    # The web seam ships `effect` straight out as JSON — a dataclass here
    # would break /peer on the web client and nowhere else, which is the
    # exact bug /btw hit.
    res, _ = await run("/peer beta hi")
    assert res.effect["kind"] == "peer_answer"
    assert isinstance(res.effect["answer"], dict)
    assert res.effect["answer"]["target"] == "beta"


@pytest.mark.asyncio
async def test_the_question_survives_verbatim_including_flags():
    _, b = await run("/peer beta why did --strict-mcp-config change?")
    assert b.calls[0][2] == "why did --strict-mcp-config change?"


@pytest.mark.asyncio
async def test_a_bare_peer_is_a_usage_error_not_a_send():
    res, b = await run("/peer")
    assert not res.ok and "usage" in res.title
    assert b.calls == []


@pytest.mark.asyncio
async def test_a_peer_with_no_question_does_not_send():
    res, b = await run("/peer beta")
    assert not res.ok and b.calls == []


@pytest.mark.asyncio
async def test_a_refusal_surfaces_as_a_failed_command():
    b = FakeBridge(answer=PeerAnswer(
        target="beta", error="beta is mid-turn. Wait for it to finish, "
                             "or /enqueue the task instead."))
    res, _ = await run("/peer beta hi", bridge=b)
    assert not res.ok
    assert "mid-turn" in res.body and "/enqueue" in res.body


def test_the_completer_marks_busy_peers():
    from aegis.commands.builtins.core import _peer_targets
    b = FakeBridge(sessions=[FakeInfo("beta"),
                             FakeInfo("gamma", state="working")])
    got = dict(_peer_targets(b))
    assert got["beta"] == "claude"
    assert "busy" in got["gamma"]


def test_render_peer_answer_leads_with_the_target():
    from aegis.render import render_peer_answer
    from aegis.tui.themes import INK, aegis_colors
    colors = aegis_colors(INK)
    text = render_peer_answer(
        PeerAnswer(answer="green", target="beta", ok=True), colors).plain
    assert text.startswith("@beta ")
    assert "green" in text
