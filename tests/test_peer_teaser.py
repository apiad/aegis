"""The teaser: a free slice of where the operator is standing.

The design bets on *pull* — send a cheap pointer, let the peer read more
if it needs to. That bet only pays if the pointer is free, which is why
the assembly is a log read and an `assemble()` call and never a model
call. `/btw` measured the window as most of its bill at 32k; the teaser
runs at a fraction of that because its job is to *place* the peer, not to
answer from.
"""
from __future__ import annotations

import pytest

from aegis.events import AssistantText, Result, UserMessage
from aegis.peer import compose, teaser
from aegis.state.session_log import EventReplay


CONVO = EventReplay(
    events=[
        UserMessage(text="why is the fork guard rejecting an idle pane?"),
        AssistantText(text="because facts_for() reads session_id, "
                           "and a resumed pane has none until its first turn"),
        Result(duration_ms=10, is_error=False),
    ],
    interrupted=False)


@pytest.fixture
def readable(monkeypatch):
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda *a, **k: CONVO)


@pytest.fixture
def unreadable(monkeypatch):
    def boom(*a, **k):
        raise OSError("no such log")
    monkeypatch.setattr("aegis.state.session_log.replay_events", boom)


# ---------- the window ---------------------------------------------------

@pytest.mark.asyncio
async def test_teaser_windows_the_source_transcript(readable):
    w = await teaser("/state", "log-1")
    assert w is not None
    assert "fork guard" in w.text


@pytest.mark.asyncio
async def test_teaser_carries_an_honest_header(readable):
    w = await teaser("/state", "log-1")
    assert w.header, "the header is how the peer sees what it is NOT seeing"


@pytest.mark.asyncio
async def test_teaser_is_far_smaller_than_a_btw_window():
    from aegis.btw.window import BUDGET_TOKENS
    from aegis.peer import TEASER_BUDGET_TOKENS
    assert TEASER_BUDGET_TOKENS < BUDGET_TOKENS / 4, (
        "the teaser places the peer; it does not let it answer from the "
        "window alone — that is what aegis_read_peer is for")


@pytest.mark.asyncio
async def test_an_unreadable_log_degrades_rather_than_failing(unreadable):
    # A missing transcript must cost the operator the teaser, never the ask.
    assert await teaser("/state", "log-1") is None


@pytest.mark.asyncio
async def test_no_state_dir_is_not_an_error():
    assert await teaser(None, "log-1") is None
    assert await teaser("/state", None) is None


# ---------- the composed body -------------------------------------------

def _body(window=None):
    return compose(source="alpha", slug="claude", prompt="is this right?",
                   window=window)


@pytest.mark.asyncio
async def test_the_teaser_rides_verbatim_not_summarised(readable):
    # If anyone ever "improves" this by summarising the window with a
    # generate() call, this assertion is what fails — and the whole cost
    # argument for pull-over-push dies with it.
    w = await teaser("/state", "log-1")
    body = _body(w)
    assert "facts_for() reads session_id" in body


@pytest.mark.asyncio
async def test_the_header_reaches_the_peer(readable):
    w = await teaser("/state", "log-1")
    assert w.header in _body(w)


def test_the_body_frames_the_operator_not_the_source_agent():
    # Tagged as though the source AGENT were asking, a peer reads it as
    # peer-to-peer delegation and skews autonomous — it goes and does
    # things instead of answering.
    body = _body()
    assert "operator" in body.lower()
    assert "alpha" in body


def test_the_body_points_at_the_pull_tool_by_name():
    assert 'aegis_read_peer("alpha")' in _body()


def test_the_body_defaults_to_reading_rather_than_answering_blind():
    # "if you need to" biases toward not calling. The burden belongs on
    # answering-without-reading.
    body = _body().lower()
    assert "unless" in body, "reading should be the default branch"


def test_the_body_scopes_the_peer_to_answering():
    # Without this the peer can decide the question warrants 20 minutes of
    # work, and "synchronous" becomes a lie with a hung pane behind it.
    assert "do not start long work" in _body().lower()


def test_a_missing_teaser_is_stated_not_hidden():
    body = _body(window=None)
    assert "could not be read" in body.lower() or "no transcript" in body.lower()
