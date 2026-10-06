"""Pure-function tests for the chunk coalescer used by the replay path
and any other consumer that ingests a list of canonical events and
wants them rendered as one block per (kind, message_id) run rather
than one block per token."""
from __future__ import annotations

from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult, Result,
)
from aegis.render import coalesce_chunks


def test_coalesce_merges_same_message_id_text_chunks():
    events = [
        AssistantText(text="hel", message_id="m1"),
        AssistantText(text="lo ", message_id="m1"),
        AssistantText(text="world", message_id="m1"),
    ]
    out = coalesce_chunks(events)
    assert len(out) == 1
    assert isinstance(out[0], AssistantText)
    assert out[0].text == "hello world"
    assert out[0].message_id == "m1"


def test_coalesce_splits_on_message_id_change():
    events = [
        AssistantText(text="a", message_id="m1"),
        AssistantText(text="b", message_id="m1"),
        AssistantText(text="c", message_id="m2"),
    ]
    out = coalesce_chunks(events)
    assert len(out) == 2
    assert out[0].text == "ab" and out[0].message_id == "m1"
    assert out[1].text == "c" and out[1].message_id == "m2"


def test_coalesce_splits_on_kind_change():
    events = [
        AssistantThinking(text="think ", message_id="m1"),
        AssistantText(text="speak", message_id="m1"),
    ]
    out = coalesce_chunks(events)
    assert len(out) == 2
    assert isinstance(out[0], AssistantThinking)
    assert isinstance(out[1], AssistantText)


def test_coalesce_preserves_non_chunk_events():
    events = [
        AssistantText(text="hi", message_id="m1"),
        ToolUse(name="Read", summary="x.py"),
        ToolResult(text="ok", is_error=False),
        Result(duration_ms=10, is_error=False),
    ]
    out = coalesce_chunks(events)
    assert len(out) == 4
    assert isinstance(out[0], AssistantText)
    assert isinstance(out[1], ToolUse)
    assert isinstance(out[2], ToolResult)
    assert isinstance(out[3], Result)


def test_coalesce_non_chunk_event_breaks_the_run():
    events = [
        AssistantText(text="a", message_id="m1"),
        ToolUse(name="Read", summary="x"),
        AssistantText(text="b", message_id="m1"),
    ]
    out = coalesce_chunks(events)
    # Same message_id but interrupted by a ToolUse → don't re-merge.
    assert len(out) == 3
    assert out[0].text == "a"
    assert isinstance(out[1], ToolUse)
    assert out[2].text == "b"


def test_coalesce_with_no_message_id_falls_back_to_kind():
    """Claude's non-streaming case: every chunk has message_id=None.
    Adjacent same-kind chunks with None ids still merge — preserves
    pre-slice-2 visual behavior for claude transcripts."""
    events = [
        AssistantText(text="hel"),
        AssistantText(text="lo"),
    ]
    out = coalesce_chunks(events)
    assert len(out) == 1
    assert out[0].text == "hello"
    assert out[0].message_id is None


def test_coalesce_empty_list():
    assert coalesce_chunks([]) == []


def test_coalesce_single_event():
    events = [AssistantText(text="hi", message_id="m1")]
    out = coalesce_chunks(events)
    assert len(out) == 1
    assert out[0].text == "hi"


def test_coalesce_preserves_usage_from_last_chunk():
    """The last chunk's usage carries the final running total — use it."""
    from aegis.events import TokenUsage
    u1 = TokenUsage(input=10, cache_creation=0, cache_read=0, output=5)
    u2 = TokenUsage(input=10, cache_creation=0, cache_read=0, output=12)
    events = [
        AssistantText(text="a", message_id="m1", usage=u1),
        AssistantText(text="b", message_id="m1", usage=u2),
    ]
    out = coalesce_chunks(events)
    assert len(out) == 1
    assert out[0].usage == u2


def test_coalesce_mixed_message_ids_and_kinds():
    """Realistic opencode-shaped stream: many tiny thought chunks under
    one id, then a brief text reply under a second id, then a tool
    call, then a result. Should produce 4 output events."""
    events = (
        [AssistantThinking(text=tok, message_id="t1")
         for tok in "Let me think".split()]
        + [AssistantText(text="ok", message_id="r1")]
        + [ToolUse(name="Read", summary="x.py")]
        + [Result(duration_ms=10, is_error=False)]
    )
    out = coalesce_chunks(events)
    assert len(out) == 4
    assert isinstance(out[0], AssistantThinking)
    assert out[0].text == "Letmethink"
    assert out[1].text == "ok"
    assert isinstance(out[2], ToolUse)
    assert isinstance(out[3], Result)
