"""Claude's partial messages (#79): recorded from claude 2.1.291 run with
``--include-partial-messages`` (tests/fixtures/claude/: a text and a Bash
call, an ultrathink turn whose thinking arrives redacted, and a subagent)."""

import json
from pathlib import Path

import pytest

from aegis.claude.stream import Delta, Ignored, Parser, Text, Thinking, parse
from aegis.transcript.entries import Fold

from .test_wire import rebuild

FIX = Path(__file__).parent / "fixtures" / "claude"
NAMES = ["tool", "thinking", "task"]


def lines(name: str) -> list[str]:
    return [ln for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln]


def streamed(ln: str) -> bool:
    return json.loads(ln)["type"] == "stream_event"


def first_prompt(name: str) -> str:
    for ln in lines(name):
        o = json.loads(ln)
        if o["type"] == "user" and o.get("isReplay"):
            return o["message"]["content"]
    raise AssertionError("no prompt")


def stream(name: str):
    """Replay a fixture the way a session does: deltas through ``live`` and
    never stored, the rest as records. Yields the fold after every line."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="deep", model="m", cwd="/x")
    own("send", text=first_prompt(name))
    for ln in lines(name):
        evs = f.parse("claude", ln)
        if evs and all(isinstance(e, Delta) for e in evs):
            f.live(evs)
        else:
            f.apply({"i": i, "ts": 1.0 + i, "src": "claude", "line": ln}, evs)
            i += 1
        yield f


def refold(name: str) -> list[dict]:
    """What a reload folds: the records, which hold no delta."""
    f, i = Fold(), 0
    recs = [
        {"src": "aegis", "kind": "spawn", "agent": "deep", "model": "m", "cwd": "/x"},
        {"src": "aegis", "kind": "send", "text": first_prompt(name)},
    ]
    recs += [{"src": "claude", "line": ln} for ln in lines(name) if not streamed(ln)]
    for r in recs:
        f.apply({"i": i, "ts": 1.0 + i, **r})
        i += 1
    return f.entries()


@pytest.mark.parametrize("name", NAMES)
def test_every_stream_event_is_one_delta_so_none_is_stored(name):
    p = Parser()
    for ln in lines(name):
        evs = p.feed(ln)
        if streamed(ln):
            assert len(evs) == 1 and isinstance(evs[0], Delta), ln[:120]
        else:
            assert not any(isinstance(e, Delta) for e in evs), ln[:120]


def test_text_deltas_add_up_to_the_block_that_closes_them():
    p, drawn = Parser(), {}
    closed = []
    for ln in lines("tool"):
        for ev in p.feed(ln):
            if isinstance(ev, Delta) and ev.key:
                assert ev.kind == "prose"
                drawn[ev.key] = drawn.get(ev.key, "") + ev.text
            if isinstance(ev, Text):
                closed.append(ev)
    assert [t.replaces for t in closed] == list(drawn)
    assert [t.text for t in closed] == list(drawn.values())
    assert drawn[closed[0].replaces].startswith("Sunlight contains all colors")


def test_redacted_thinking_draws_nothing_live_and_its_text_still_streams():
    p, keys = Parser(), []
    for ln in lines("thinking"):
        for ev in p.feed(ln):
            if isinstance(ev, Delta) and ev.key:
                keys.append((ev.key, ev.kind))
            if isinstance(ev, Thinking):
                assert ev.replaces and ev.replaces.endswith(".0")
            if isinstance(ev, Text):
                assert ev.replaces and ev.replaces.endswith(".1")
    # claude 2.1.291 sends empty thinking deltas: nothing to draw.
    assert {k for k, _ in keys} == {keys[0][0]} and keys[0][0].endswith(".1")
    assert {kind for _, kind in keys} == {"prose"} and len(keys) > 20


def test_a_thinking_delta_with_text_draws_a_thinking_row():
    p = Parser()
    start = {"type": "message_start", "message": {"id": "msg_1"}}
    p.feed(json.dumps({"type": "stream_event", "event": start}))
    ev = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "hmm"},
    }
    assert p.feed(json.dumps({"type": "stream_event", "event": ev})) == [
        Delta(key="msg_1.0", kind="thinking", text="hmm")
    ]
    sub = {"type": "stream_event", "event": ev, "parent_tool_use_id": "toolu_x"}
    assert p.feed(json.dumps(sub)) == [Delta(key="", kind="", text="")]


def test_the_stateless_parse_still_ignores_a_delta():
    (ln,) = [x for x in lines("tool") if "text_delta" in x][:1]
    assert parse(ln) == [Ignored(type="stream_event")]


@pytest.mark.parametrize("name", NAMES)
def test_live_deltas_end_where_a_reload_starts(name):
    *_, f = stream(name)
    assert f.entries() == refold(name)


def test_text_is_drawn_before_its_block_closes_and_replaced_once_it_does():
    seen = []
    for f in stream("tool"):
        prose = [e for e in f.entries() if e["kind"] == "prose"]
        seen.append([(e["id"], e["md"]) for e in prose])
    partial = [s for s in seen if s and not s[0][0].startswith("e")]
    assert partial and partial[0][0][1] == "S"  # the first delta, drawn
    assert seen[-1][0][1].startswith("Sunlight contains all colors")
    assert all(len(s) <= 2 for s in seen), "never two rows for one block"


@pytest.mark.parametrize("name", NAMES)
def test_a_delta_between_two_moments_of_a_stream_rebuilds_the_later_one(name):
    seen: list[tuple[list[dict], int]] = []
    for f in stream(name):
        now = f.snapshot()
        copy = json.loads(json.dumps(now["entries"]))
        for held, rev in seen[-40:]:
            assert rebuild(held, f.snapshot(rev)) == copy
        seen.append((copy, now["rev"]))
