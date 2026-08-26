"""The gate is the cost control and the noise control at once.

anthropics/claude-code#56346: gating on turn count produces 10+ identical
recaps in a row. Gate on substrate movement instead.
"""
import asyncio
import time

import pytest

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.recap import Recap
from aegis.recap.gate import should_recap

from tests.digest_harness import build_session

MOVED = TurnFacts(repos=(RepoDelta(name="aegis", files_written=1,
                                   commits=(CommitLine("a1", "feat: x"),)),))
STILL = TurnFacts(assistant_tail="Here is what that function does.")


def test_a_moving_turn_recaps():
    assert should_recap(MOVED, last_line="", enabled=True) is True


def test_a_read_only_turn_does_not():
    assert should_recap(STILL, last_line="", enabled=True) is False


def test_a_disabled_recap_never_fires():
    assert should_recap(MOVED, last_line="", enabled=False) is False


def test_an_errored_digest_does_not_fire():
    facts = TurnFacts(repos=MOVED.repos, error="git exploded")
    assert should_recap(facts, last_line="", enabled=True) is False


@pytest.mark.asyncio
async def test_the_auto_recap_does_not_block_the_turn(tmp_path,
                                                      monkeypatch):
    """The measured ~7s floor is why this is detached. If the recap were
    awaited, this turn would take at least as long as the sleep."""
    async def slow(**_kw):
        await asyncio.sleep(0.5)
        return Recap(line="done", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", slow)
    s, _ = build_session(tmp_path, agents={})
    s.recap_enabled = True
    s.last_facts = MOVED
    monkeypatch.setattr(s.digest, "build", _facts_returning(MOVED))
    t0 = time.monotonic()
    await s.send_and_wait("hello")
    assert time.monotonic() - t0 < 0.4    # the turn did not wait
    s._cancel_recap()


def _facts_returning(facts):
    async def _build(**_kw):
        return facts
    return _build


@pytest.mark.asyncio
async def test_a_new_turn_cancels_an_in_flight_recap(tmp_path, monkeypatch):
    """A late recap describes a transcript that has moved on."""
    seen = []

    async def slow(**_kw):
        await asyncio.sleep(5)
        return Recap(line="late", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", slow)
    s, _ = build_session(tmp_path, agents={})
    s.recap_enabled = True
    monkeypatch.setattr(s.digest, "build", _facts_returning(MOVED))
    s.on_recap = lambda _s, r: seen.append(r)
    await s.send_and_wait("one")
    await s.send_and_wait("two")
    await asyncio.sleep(0.05)
    assert seen == []
    s._cancel_recap()


@pytest.mark.asyncio
async def test_an_identical_line_is_dropped(tmp_path, monkeypatch):
    """The #56346 failure arriving by another road."""
    seen = []

    async def same(**_kw):
        return Recap(line="Wrote the same thing.", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", same)
    s, _ = build_session(tmp_path, agents={})
    s.recap_enabled = True
    monkeypatch.setattr(s.digest, "build", _facts_returning(MOVED))
    s.on_recap = lambda _s, r: seen.append(r)
    await s.send_and_wait("one")
    await asyncio.sleep(0.05)
    await s.send_and_wait("two")
    await asyncio.sleep(0.05)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_the_recap_never_reaches_the_agent(tmp_path, monkeypatch):
    """It is for the operator. Feeding it back would make every turn open
    by reading a summary of itself — and worse, a logged recap enters the
    window the NEXT recap assembles, so summaries compound.

    Asserts on the substrate (what the harness was sent), not on a
    rendered string: a source-grep would pin the text and survive the bug.
    """
    async def fast(**_kw):
        return Recap(line="RECAP-SENTINEL-XYZ", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fast)
    s, harness = build_session(tmp_path, agents={})
    s.recap_enabled = True
    monkeypatch.setattr(s.digest, "build", _facts_returning(MOVED))
    await s.send_and_wait("one")
    await asyncio.sleep(0.05)
    await s.send_and_wait("two")

    assert harness.sent
    assert not any("RECAP-SENTINEL-XYZ" in m for m in harness.sent)
