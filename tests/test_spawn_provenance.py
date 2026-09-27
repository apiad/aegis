"""`/spawn <prompt>` carries where the operator was standing.

`/spawn opus please verify this test` used to hand the new agent three
words and no referent. It now arrives with the same three things `@peer`
sends — provenance of *place*, a bounded tail of the source transcript,
and a pointer to `aegis_read_peer` for the rest — with one deliberate
inversion: a peer is told not to start long work, and a spawn is told to
do it.

Spec: `docs/superpowers/specs/2026-08-10-aegis-spawn-with-provenance-design.md`
"""
from __future__ import annotations

import pytest

from aegis.commands import CommandContext, dispatch
from aegis.peer import compose_spawn

from tests.test_slash_commands import FakeBridge


TAIL = ("user: the round-trip test keeps failing on the second assert\n"
        "assistant: because assemble() fills backwards from the newest event")
HEADER = "last 3 of 143 turns"


def _composed(prompt="please verify this test"):
    return compose_spawn(source="alpha", slug="opus", prompt=prompt,
                         tail=TAIL, header=HEADER)


# ---------- the composed body -------------------------------------------

def test_compose_spawn_names_the_place_it_came_from():
    body = _composed()
    assert "alpha" in body and "opus" in body


def test_compose_spawn_frames_the_operator_as_the_author():
    """Provenance of place, not author.

    Tagged as though the source *agent* were delegating, the new agent
    reads it as peer-to-peer orders and skews toward pleasing the peer
    instead of the operator.
    """
    body = _composed()
    assert "operator" in body.lower()


def test_compose_spawn_carries_the_tail_and_its_honest_header():
    body = _composed()
    assert "assemble() fills backwards" in body
    assert HEADER in body, ("the header is how the agent sees what it is "
                            "NOT seeing")


def test_the_pushed_tail_survives_the_transcript_renderer():
    """The tail is one transcript item per line, and the operator has to be
    able to read it back in the pane.

    `--- tail of X ---` markers left the tail as an ordinary Markdown
    paragraph, so Rich folded every newline and the whole window rendered as
    one run-on line with the speaker labels glued together. A fenced block
    keeps the lines, and keeps a `<tag>` in the tail from opening an HTML
    block on top of that.
    """
    from rich.console import Console

    from aegis.render import render_user_block
    from aegis.tui.themes import INK, aegis_colors

    tail = ("user: the round-trip test keeps failing\n"
            "<system-reminder>never stage broadly</system-reminder>\n"
            "assistant: because assemble() fills backwards")
    body = compose_spawn(source="alpha", slug="opus", prompt="verify it",
                         tail=tail, header=HEADER)
    con = Console(record=True, width=100)
    con.print(render_user_block(body, aegis_colors(INK), 100))
    out = con.export_text()

    for line in tail.splitlines():
        assert line in out, f"the renderer dropped or reflowed: {line!r}"
    glued = [ln for ln in out.splitlines()
             if "keeps failing" in ln and "fills backwards" in ln]
    assert not glued, "the tail folded into one line"


def test_compose_spawn_marks_where_the_tail_stops():
    """The agent has to be able to tell the pushed window from the task.
    Whatever the delimiter is, opening and closing it must not be the same
    ambiguous run of hyphens the task line could also contain."""
    body = compose_spawn(source="alpha", slug="opus",
                         prompt="verify this test", tail=TAIL, header=HEADER)
    lines = body.splitlines()
    fences = [i for i, ln in enumerate(lines) if ln.startswith("```")]
    assert len(fences) == 2, "the tail needs exactly one opening and one close"
    inside = "\n".join(lines[fences[0] + 1:fences[1]])
    assert inside == TAIL
    assert "The operator's task" not in inside


def test_compose_spawn_points_at_the_pull():
    body = _composed()
    assert 'aegis_read_peer("alpha")' in body


def test_compose_spawn_ends_with_the_operators_words():
    """The question is last so it is the freshest thing in the window."""
    body = _composed("please verify this test")
    assert body.rstrip().endswith("please verify this test")


def test_compose_spawn_tells_it_to_do_the_work():
    """The one place this inverts `@peer`, which says the opposite."""
    body = _composed()
    assert "Do not start long work" not in body


def test_compose_spawn_offers_a_way_back():
    body = _composed()
    assert "aegis_handoff" in body


# ---------- the command wiring ------------------------------------------

class ReadableBridge(FakeBridge):
    """A bridge whose source pane has a transcript worth carrying."""

    def __init__(self, ok=True, text=TAIL, boom=False):
        super().__init__()
        self._read = {"ok": ok, "text": text, "header": HEADER, "error": ""}
        self._boom = boom
        self.read_calls: list = []

    async def read_peer(self, handle, turns=12, budget_tokens=None,
                        item_chars=None):
        if self._boom:
            raise RuntimeError("log is gone")
        self.read_calls.append((handle, turns, budget_tokens, item_chars))
        return dict(self._read)


def _ctx(bridge):
    return CommandContext(bridge=bridge, handle="alpha")


@pytest.mark.asyncio
async def test_spawn_composes_the_opening_prompt_from_the_source_tail():
    bridge = ReadableBridge()
    res = await dispatch("/spawn opus please verify this test", _ctx(bridge))
    assert res.ok
    profile, opening, spawned_by = bridge.spawned[0]
    assert profile == "opus" and spawned_by == "alpha"
    assert "assemble() fills backwards" in opening
    assert opening.rstrip().endswith("please verify this test")


@pytest.mark.asyncio
async def test_spawn_reads_its_own_pane_at_teaser_width():
    """Not `read_peer`'s own defaults, which are twelve times wider.

    Measured on a real 410KB transcript: at ``READ_BUDGET_TOKENS`` a
    3-turn window came back at 95,346 chars — ~24k tokens of preamble in
    front of the three words the operator typed, on every spawn. The
    turn bound does not save you, because one long in-flight turn is a
    single turn.
    """
    from aegis.peer import (
        TEASER_BUDGET_TOKENS, TEASER_ITEM_CHARS, TEASER_MAX_TURNS,
    )
    bridge = ReadableBridge()
    await dispatch("/spawn opus verify this", _ctx(bridge))
    assert bridge.read_calls == [
        ("alpha", TEASER_MAX_TURNS, TEASER_BUDGET_TOKENS, TEASER_ITEM_CHARS)]


@pytest.mark.asyncio
async def test_the_teaser_window_is_actually_small():
    """The end of the chain, not the request: a real fat transcript,
    through the real ``read_window``, must fit a preamble."""
    from aegis.btw.window import BUDGET_TOKENS
    from aegis.events import AssistantText, Result, ToolResult, UserMessage
    from aegis.peer import (
        TEASER_BUDGET_TOKENS, TEASER_ITEM_CHARS, TEASER_MAX_TURNS, read_window,
    )
    from aegis.state.session_log import EventReplay

    # One enormous turn still in flight — the shape that defeats the turn
    # bound and let 24k tokens through.
    fat = EventReplay(events=[UserMessage(text="go"),
                              *(ToolResult(text="x" * 20_000,
                                           tool_call_id=str(i),
                                           is_error=False)
                                for i in range(40)),
                              AssistantText(text="done"),
                              Result(duration_ms=1, is_error=False)],
                      interrupted=False)
    import aegis.state.session_log as sl
    real = sl.replay_events
    sl.replay_events = lambda *a, **k: fat
    try:
        w = await read_window("/state", "log-1", TEASER_MAX_TURNS,
                              budget_tokens=TEASER_BUDGET_TOKENS,
                              item_chars=TEASER_ITEM_CHARS)
    finally:
        sl.replay_events = real
    assert w["ok"]
    tokens = len(w["text"]) // 4
    assert tokens <= TEASER_BUDGET_TOKENS * 1.1, f"{tokens} tokens"
    assert tokens < BUDGET_TOKENS / 10


@pytest.mark.asyncio
async def test_spawn_reports_the_operators_own_words_not_the_composed_body():
    """The confirmation line echoes what was typed. Printing the whole
    composed preamble back into the pane would bury it."""
    bridge = ReadableBridge()
    res = await dispatch("/spawn opus verify this", _ctx(bridge))
    assert "prompt: verify this" in res.body
    assert "aegis_read_peer" not in res.body


@pytest.mark.asyncio
async def test_spawn_without_a_prompt_carries_nothing():
    bridge = ReadableBridge()
    await dispatch("/spawn opus", _ctx(bridge))
    assert bridge.spawned == [("opus", None, "alpha")]
    assert not bridge.read_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"ok": False}, {"text": ""}, {"boom": True}])
async def test_spawn_falls_back_to_the_bare_prompt_with_no_tail(kw):
    """The preamble rides on the tail. Provenance pointing at a transcript
    nobody can read buys the new agent a failed tool call and a paragraph
    of confusion."""
    bridge = ReadableBridge(**kw)
    res = await dispatch("/spawn opus verify this", _ctx(bridge))
    assert res.ok
    assert bridge.spawned == [("opus", "verify this", "alpha")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kw,why", [
    ({"ok": False}, "unreadable"),
    ({"text": ""}, "empty"),
    ({"boom": True}, "unreadable"),
])
async def test_spawn_says_when_the_new_agent_arrived_blind(kw, why):
    """A degraded spawn has to be visible at the moment it happens.

    All five fall-back paths returned the bare prompt with no trace, and
    `Origin` is not in the session log, so afterwards there is no way to tell
    a spawn that carried its tail from one that did not — the rate was not
    measurable even across 341 real logs.
    """
    bridge = ReadableBridge(**kw)
    res = await dispatch("/spawn opus verify this", _ctx(bridge))
    assert res.ok, "a missing tail costs the preamble, never the spawn"
    assert "no tail" in res.body
    assert why in res.body


@pytest.mark.asyncio
async def test_spawn_says_nothing_about_the_tail_when_it_carried_one():
    """The note is a warning, not a field. A healthy spawn stays quiet."""
    bridge = ReadableBridge()
    res = await dispatch("/spawn opus verify this", _ctx(bridge))
    assert "no tail" not in res.body


@pytest.mark.asyncio
async def test_spawn_without_a_prompt_is_not_reported_as_blind():
    """Nothing was asked, so there is no referent to be missing."""
    bridge = ReadableBridge()
    res = await dispatch("/spawn opus", _ctx(bridge))
    assert "no tail" not in res.body


@pytest.mark.asyncio
async def test_spawn_survives_a_bridge_with_no_read_peer():
    """`read_peer` is deliberately off the AppBridge Protocol, so a
    frontend may not have it — that must cost the preamble, never the
    spawn."""
    bridge = FakeBridge()
    assert not hasattr(bridge, "read_peer")
    res = await dispatch("/spawn opus verify this", _ctx(bridge))
    assert res.ok
    assert bridge.spawned == [("opus", "verify this", "alpha")]
