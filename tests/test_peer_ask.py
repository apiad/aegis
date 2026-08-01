"""`@peer` — asking an idle peer, from where you're standing.

Two halves, both here: ``AgentSession.capture_next_reply`` (the piece that
has never existed — ``runner.py`` reaches for ``session_send_and_await`` on
the bridge and no production class defined it) and the refusal matrix that
keeps a peer ask from ever disturbing the conversation it sits beside.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.peer import PeerAnswer


class FakeSession:
    """Replays a canned event stream, one turn per ``send``."""

    def __init__(self, turns):
        # turns: list of event-lists, consumed one per send()
        self._turns = [list(t) for t in turns]
        self.sent: list[str] = []
        self.session_id = "sid"

    async def start(self): ...
    async def close(self): ...

    async def send(self, t):
        self.sent.append(t)

    async def events(self):
        for turn in self._turns:
            for e in turn:
                await asyncio.sleep(0)
                yield e


class FakeAgent:
    harness = "claude-code"
    model = "opus"
    effort = "high"
    permission = "full"
    prompt = None


def _session(turns, handle="beta") -> AgentSession:
    return AgentSession(FakeSession(turns), agent=FakeAgent(),
                        agent_slug="default", handle=handle)


# ---------- PeerAnswer ---------------------------------------------------

def test_footer_names_the_target_and_the_price():
    a = PeerAnswer(answer="yes", target="lucid-knuth", model="opus",
                   duration_ms=12400, cost_usd=0.0312, ok=True)
    assert a.footer == "lucid-knuth · opus · 12.4s · $0.0312"


def test_footer_drops_what_it_does_not_know():
    assert PeerAnswer(target="beta").footer == "beta"


def test_peer_answer_is_asdict_able():
    # The web seam ships `effect` straight out as JSON, so the effect
    # payload must be a plain dict — the exact bug /btw hit.
    from dataclasses import asdict
    assert asdict(PeerAnswer(target="beta"))["target"] == "beta"


# ---------- capture_next_reply ------------------------------------------

@pytest.mark.asyncio
async def test_capture_returns_the_whole_assistant_message():
    s = _session([[AssistantText(text="part one "),
                   AssistantText(text="part two"),
                   Result(duration_ms=10, is_error=False)]])
    fut = s.capture_next_reply()
    await s.send("q")
    await s._task
    assert await asyncio.wait_for(fut, 1) == "part one part two"


@pytest.mark.asyncio
async def test_capture_ignores_subagent_text():
    # A peer that runs a Task subagent must not fold the subagent's
    # narration into the reply the operator reads.
    s = _session([[AssistantText(text="mine"),
                   AssistantText(text="theirs", parent_tool_use_id="tu_1"),
                   Result(duration_ms=10, is_error=False)]])
    fut = s.capture_next_reply()
    await s.send("q")
    await s._task
    assert await asyncio.wait_for(fut, 1) == "mine"


@pytest.mark.asyncio
async def test_capture_resolves_once_and_detaches():
    s = _session([[AssistantText(text="first"),
                   Result(duration_ms=10, is_error=False)],
                  [AssistantText(text="second"),
                   Result(duration_ms=10, is_error=False)]])
    before = len(s._extra_event_observers)
    fut = s.capture_next_reply()
    await s.send("q")
    await s._task
    assert await asyncio.wait_for(fut, 1) == "first"
    # the observer is gone, so the next turn cannot re-resolve it
    assert len(s._extra_event_observers) == before
    await s.send("q2")
    await s._task
    assert fut.result() == "first"


# ---------- the refusal matrix ------------------------------------------

def _mgr(sessions: dict[str, AgentSession]) -> SessionManager:
    mgr = SessionManager(agents={"default": FakeAgent()},
                         default_agent="default",
                         make_session=lambda *a, **k: FakeSession([[]]),
                         mcp=None)
    mgr._sessions = list(sessions.values())
    return mgr


@pytest.mark.asyncio
async def test_refuses_an_unknown_target():
    mgr = _mgr({"alpha": _session([[]], handle="alpha")})
    a = await mgr.peer_ask("alpha", "nobody", "hi")
    assert not a.ok
    assert "nobody" in a.error


@pytest.mark.asyncio
async def test_an_unknown_target_names_the_live_ones():
    """refusal()'s contract is that every refusal names the alternative,
    and this was the branch that didn't — the one people actually hit,
    because the feature is *called* @peer everywhere it is discussed, so
    `@peer <question>` asks for a session literally named "peer"."""
    mgr = _mgr({"alpha": _session([[]], handle="alpha"),
                "beta": _session([[]], handle="beta")})
    a = await mgr.peer_ask("alpha", "peer", "hi")
    assert not a.ok
    assert "beta" in a.error, a.error
    # The asker is not offered: you cannot @ yourself.
    assert "alpha" not in a.error.split("Open now:")[-1], a.error


@pytest.mark.asyncio
async def test_refuses_asking_itself_and_points_at_btw():
    mgr = _mgr({"alpha": _session([[]], handle="alpha")})
    a = await mgr.peer_ask("alpha", "alpha", "hi")
    assert not a.ok and "/btw" in a.error


@pytest.mark.asyncio
async def test_refuses_a_busy_target_and_names_enqueue():
    from aegis.tui.state import AgentState
    beta = _session([[]], handle="beta")
    beta.state = AgentState.working
    mgr = _mgr({"alpha": _session([[]], handle="alpha"), "beta": beta})
    a = await mgr.peer_ask("alpha", "beta", "hi")
    assert not a.ok
    assert "mid-turn" in a.error and "/enqueue" in a.error


@pytest.mark.asyncio
async def test_a_target_that_goes_busy_mid_ask_is_withdrawn_not_waited_on():
    """The idle check and the delivery are two moments, and a peer can go
    busy between them (an inbox callback, a monitor, the operator typing
    into its tab). Found by mutation: with the guard removed the ask does
    not fail, it *hangs* for the full timeout — because a queued message
    resolves at the peer's next turn boundary, not now. So trust the
    delivery receipt, not the earlier check, and withdraw the message.
    """
    from aegis.queue.schema import Delivery

    beta = _session([[]], handle="beta")
    sent, dropped = [], []

    async def _queued_deliver(msg):
        sent.append(msg)
        return Delivery(disposition="queued", depth=1)

    beta.deliver = _queued_deliver
    beta.cancel_pending = lambda m: (dropped.append(m), True)[1]

    mgr = _mgr({"alpha": _session([[]], handle="alpha"), "beta": beta})
    a = await asyncio.wait_for(mgr.peer_ask("alpha", "beta", "hi"), 5)
    assert not a.ok and "mid-turn" in a.error
    assert dropped == sent, "the queued message must be withdrawn"


@pytest.mark.asyncio
async def test_a_busy_source_is_not_a_refusal():
    """The guard reads the target, never the source. Asking an idle peer
    while your own tab is mid-turn is the whole point."""
    from aegis.tui.state import AgentState
    alpha = _session([[]], handle="alpha")
    alpha.state = AgentState.working
    beta = _session([[AssistantText(text="green"),
                      Result(duration_ms=10, is_error=False)]], handle="beta")
    mgr = _mgr({"alpha": alpha, "beta": beta})
    a = await mgr.peer_ask("alpha", "beta", "is the build green?")
    assert a.ok and a.answer == "green"
    assert a.target == "beta"
