"""`aegis_read_peer` — the pull half of the design, and `--cc`.

The tool unlocks no new capability: transcripts are plain JSONL under
`.aegis/state/sessions/`, inside the project root, and every agent has
Read and Bash today. What is missing is *addressing* — the log id carries
the session's BIRTH handle (`state/session_log.py:83`) and is never
renamed, so an agent cannot get from a peer's current handle to its file
without knowing that. This is discoverability, not permission.
"""
from __future__ import annotations

import pytest

from aegis.events import AssistantText, Result, UserMessage
from aegis.peer import read_window
from aegis.state.session_log import EventReplay


CONVO = EventReplay(
    events=[
        UserMessage(text="what schema did you settle on?"),
        AssistantText(text="a flat dict keyed by circuito id"),
        Result(duration_ms=10, is_error=False),
    ],
    interrupted=False)


@pytest.fixture
def readable(monkeypatch):
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda *a, **k: CONVO)


@pytest.mark.asyncio
async def test_read_window_returns_the_transcript(readable):
    got = await read_window("/state", "log-1", turns=12)
    assert got["ok"]
    assert "flat dict keyed by circuito id" in got["text"]


@pytest.mark.asyncio
async def test_read_window_carries_the_header(readable):
    got = await read_window("/state", "log-1", turns=12)
    assert got["header"]


@pytest.mark.asyncio
async def test_read_window_is_wider_than_the_teaser(readable):
    """The teaser places the peer; the pull is what it reads when placing
    was not enough. A pull no bigger than the teaser would make the whole
    push-vs-pull split pointless."""
    from aegis.peer import READ_BUDGET_TOKENS, TEASER_BUDGET_TOKENS
    assert READ_BUDGET_TOKENS > TEASER_BUDGET_TOKENS * 4


@pytest.mark.asyncio
async def test_an_unreadable_log_is_an_error_not_a_raise(monkeypatch):
    def boom(*a, **k):
        raise OSError("gone")
    monkeypatch.setattr("aegis.state.session_log.replay_events", boom)
    got = await read_window("/state", "log-1")
    assert not got["ok"] and got["error"]


@pytest.mark.asyncio
async def test_a_missing_log_id_names_the_reason():
    got = await read_window("/state", None)
    assert not got["ok"] and "transcript" in got["error"].lower()


# ---------- --cc ---------------------------------------------------------

class FakeSource:
    """A source session that records what got delivered into it."""

    def __init__(self):
        self.delivered: list = []

    async def deliver(self, msg):
        from aegis.queue.schema import Delivery
        self.delivered.append(msg)
        return Delivery(disposition="landed", depth=0)


@pytest.mark.asyncio
async def test_cc_delivers_the_answer_into_the_source():
    from aegis.peer import PeerAnswer, cc_into
    src = FakeSource()
    await cc_into(src, PeerAnswer(answer="green", target="beta", ok=True))
    assert len(src.delivered) == 1
    assert "green" in src.delivered[0].body
    assert "beta" in src.delivered[0].body


@pytest.mark.asyncio
async def test_cc_is_tagged_as_coming_from_the_peer_not_the_operator():
    from aegis.peer import PeerAnswer, cc_into
    src = FakeSource()
    await cc_into(src, PeerAnswer(answer="green", target="beta", ok=True))
    assert "beta" in src.delivered[0].sender


@pytest.mark.asyncio
async def test_a_failed_ask_is_never_cc_d():
    from aegis.peer import PeerAnswer, cc_into
    src = FakeSource()
    await cc_into(src, PeerAnswer(target="beta", error="beta is mid-turn"))
    assert src.delivered == [], "a refusal is not news the source must pay for"


@pytest.mark.asyncio
async def test_cc_survives_a_source_that_cannot_take_it():
    """cc is a courtesy on top of an answer the operator already has —
    it must never turn a successful ask into a failed one."""
    from aegis.peer import PeerAnswer, cc_into

    class Broken:
        async def deliver(self, msg):
            raise RuntimeError("pane is gone")

    await cc_into(Broken(), PeerAnswer(answer="green", target="beta", ok=True))


# ---------- the command flag --------------------------------------------

@pytest.mark.asyncio
async def test_the_cc_flag_reaches_the_bridge():
    from aegis.commands import CommandContext, dispatch
    from aegis.peer import PeerAnswer

    class B:
        def __init__(self):
            self.calls = []

        def list_sessions(self):
            return []

        async def peer_ask(self, from_handle, target, prompt, *, cc=False):
            self.calls.append((target, prompt, cc))
            return PeerAnswer(answer="ok", target=target, ok=True)

    b = B()
    await dispatch("/peer beta --cc what schema?",
                   CommandContext(bridge=b, handle="alpha"))
    assert b.calls == [("beta", "what schema?", True)]

    b2 = B()
    await dispatch("/peer beta what schema?",
                   CommandContext(bridge=b2, handle="alpha"))
    assert b2.calls == [("beta", "what schema?", False)]


@pytest.mark.asyncio
async def test_at_sugar_carries_the_cc_flag():
    from aegis.commands import classify_input
    assert classify_input("@beta --cc what schema?") == (
        "command", "/peer beta --cc what schema?")


@pytest.mark.asyncio
async def test_cc_does_not_swallow_cancellation():
    """ESC on a deferred `@peer` must cancel the cc too.

    This holds only because `CancelledError` derives from `BaseException`
    (since 3.8), so `cc_into`'s `except Exception` does not catch it. That
    handler reads like an over-narrow one somebody should tidy — widen it
    to `except BaseException` or a bare `except:` and cancellation is
    silently swallowed: the peer's turn finishes regardless and its answer
    lands in a conversation the operator walked away from minutes earlier.

    A comment alone loses to a plausible-looking cleanup, so this is a
    test.
    """
    import asyncio

    from aegis.peer import PeerAnswer, cc_into

    class Cancelling:
        async def deliver(self, msg):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cc_into(Cancelling(),
                      PeerAnswer(answer="green", target="beta", ok=True))


def test_peer_is_deferred_with_an_honest_cancel_note():
    """`/peer` must not be awaited in a frontend's input handler: it awaits
    a whole peer turn, bounded by PEER_ASK_TIMEOUT_S = 300. Awaiting that
    inside a Textual message handler freezes the pane — which would defeat
    the feature's best property, since asking an idle peer *while your own
    tab is mid-turn* is the whole use case.
    """
    from aegis.commands import REGISTRY, parse
    cmd = REGISTRY["peer"]
    assert cmd.deferred
    args = parse(cmd.spec, "beta is the build green?")
    note = cmd.resolved_cancel_note(args)
    assert "beta" in note, "{handle} must agree with Arg('handle', …)"
    assert "cancelled" not in note.lower(), (
        "the peer's turn is real and completes regardless — nothing was "
        "cancelled, the operator stopped waiting")
