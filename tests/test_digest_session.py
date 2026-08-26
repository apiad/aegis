"""The digest must ride the real turn path — and the recording hook must
be somewhere replay does not reach."""
import pytest

from aegis.digest.models import TurnFacts
from aegis.events import AssistantText, Result, ToolUse

from tests.digest_harness import build_session


def test_session_exposes_a_digest_collector(tmp_path):
    s, _ = build_session(tmp_path)
    assert s.digest is not None
    assert s.last_facts is None


@pytest.mark.asyncio
async def test_a_turn_produces_facts_and_fires_the_observer(tmp_path):
    seen = []
    s, _ = build_session(tmp_path)
    s.on_facts = lambda _s, f: seen.append(f)
    await s.send_and_wait("hello")
    assert isinstance(s.last_facts, TurnFacts)
    assert len(seen) == 1
    assert seen[0] is s.last_facts


@pytest.mark.asyncio
async def test_writes_during_a_turn_reach_the_collector(tmp_path):
    """A Write tool_use must be recorded, via the same write_target path
    the repo tracker already uses."""
    s, _ = build_session(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    s._fire_event(ToolUse(name="Write", summary=str(target),
                          tool_call_id="t1",
                          raw_input={"file_path": str(target)}))
    assert s.digest._tracked


@pytest.mark.asyncio
async def test_each_turn_starts_from_a_clean_digest(tmp_path):
    s, _ = build_session(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    s._fire_event(ToolUse(name="Write", summary=str(target),
                          tool_call_id="t1",
                          raw_input={"file_path": str(target)}))
    await s.send_and_wait("go")
    first = s.last_facts
    await s.send_and_wait("again")
    assert first is not s.last_facts
    assert s.last_facts.repos == ()


@pytest.mark.asyncio
async def test_the_tail_excludes_subagent_narration(tmp_path):
    """A subagent's narration is not this turn's answer. The queue's
    result capture had to learn this the hard way.

    The events are emitted BY THE FAKE HARNESS during the turn, because
    `own_text_parts` fills inside `_run_turn`'s event loop — firing them
    via `_fire_event` would pass against a broken implementation.
    """
    s, _ = build_session(tmp_path, script=[
        AssistantText(text="I dispatched a subagent."),
        AssistantText(text="SUBAGENT-CHATTER", parent_tool_use_id="task-1"),
        AssistantText(text=" Done."),
        Result(duration_ms=1, is_error=False),
    ])
    await s.send_and_wait("go")
    assert "SUBAGENT-CHATTER" not in s.last_facts.assistant_tail
    assert "I dispatched a subagent." in s.last_facts.assistant_tail
    assert "Done." in s.last_facts.assistant_tail


@pytest.mark.asyncio
async def test_a_broken_collector_does_not_break_the_turn(tmp_path,
                                                          monkeypatch):
    """Best-effort by contract: the turn completes regardless."""
    s, _ = build_session(tmp_path)

    async def boom(**_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(s.digest, "build", boom)
    await s.send_and_wait("hello")          # must not raise
    assert s.last_facts is not None
    assert s.last_facts.error
