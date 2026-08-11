"""The pure descriptor registry: name normalisation, glyphs, lines, targets."""
from __future__ import annotations

import pytest

from aegis.comms.descriptors import (
    CONVERSATION, Target, aegis_describe, aegis_family, aegis_glyph,
    aegis_target, descriptor_for,
)


@pytest.mark.parametrize("name", [
    "aegis_handoff",                  # what the middleware sees
    "mcp__aegis__aegis_handoff",      # what the claude transcript carries
])
def test_both_name_shapes_resolve_to_the_same_descriptor(name):
    d = descriptor_for(name)
    assert d is not None
    assert d.verb == "handoff"


@pytest.mark.parametrize("name", ["Bash", "Read", "", "mcp__other__thing",
                                  "aegis_not_a_tool"])
def test_non_aegis_names_have_no_descriptor(name):
    assert descriptor_for(name) is None
    assert aegis_glyph(name) is None
    assert aegis_describe(name) is None


def test_handoff_names_the_peer_and_quotes_the_context():
    args = {"from_handle": "me", "target_handle": "weary-turing",
            "context": "the parser is green, the render is yours"}
    assert aegis_glyph("aegis_handoff", args) == "⇄"
    assert aegis_describe("aegis_handoff", args) == (
        'weary-turing · "the parser is green, the render is yours"')
    assert aegis_target("aegis_handoff", args) == Target("agent",
                                                         "weary-turing")
    assert aegis_family("aegis_handoff") == CONVERSATION


def test_an_interrupting_handoff_takes_the_cut_glyph():
    args = {"target_handle": "weary-turing", "context": "stop, wrong branch",
            "interrupt": True}
    assert aegis_glyph("aegis_handoff", args) == "⇅"
    assert aegis_describe("aegis_handoff", args) == (
        'weary-turing · cut · "stop, wrong branch"')


def test_spawn_shows_profile_at_host_because_the_handle_does_not_exist_yet():
    args = {"agent": "main", "prompt": "audit the ledger", "host": "vps",
            "from_handle": "me"}
    assert aegis_glyph("aegis_spawn", args) == "✧"
    assert aegis_describe("aegis_spawn", args) == 'main@vps · "audit the ledger"'
    assert aegis_target("aegis_spawn", args) == Target("agent", "main")


def test_spawn_without_a_host_omits_the_at_suffix():
    args = {"agent": "main", "prompt": "audit the ledger"}
    assert aegis_describe("aegis_spawn", args) == 'main · "audit the ledger"'


def test_long_excerpts_are_collapsed_and_truncated():
    args = {"target_handle": "peer",
            "context": "word " * 40}
    line = aegis_describe("aegis_handoff", args)
    assert line.startswith('peer · "word word')
    assert line.endswith('…"')
    assert "  " not in line


def test_missing_arguments_never_raise_and_never_leave_stray_separators():
    for verb in ("handoff", "spawn", "fork", "close", "enqueue", "delegate",
                 "cancel"):
        line = aegis_describe(f"aegis_{verb}", {})
        assert line is not None
        assert not line.startswith("·")
        assert not line.endswith("·")


def test_queue_verbs_point_at_the_queue_and_cancel_at_the_task():
    enq = {"queue": "general", "payload": "port the fixtures",
           "from_handle": "me"}
    assert aegis_glyph("aegis_enqueue", enq) == "⇉"
    assert aegis_describe("aegis_enqueue", enq) == 'general · "port the fixtures"'
    assert aegis_target("aegis_enqueue", enq) == Target("queue", "general")

    dele = {"queue": "general", "payload": "resolve the merge"}
    assert aegis_describe("aegis_delegate", dele) == (
        'general · blocking · "resolve the merge"')

    assert aegis_glyph("aegis_cancel", {"task_id": "01K4TZ"}) == "⇎"
    assert aegis_target("aegis_cancel", {"task_id": "01K4TZ"}) == Target(
        "queue", "01K4TZ")


def test_fork_and_close_name_the_handle_they_act_on():
    fork = {"target_handle": "weary-turing", "prompt": "take the perf angle"}
    assert aegis_glyph("aegis_fork", fork) == "✧"
    assert aegis_describe("aegis_fork", fork) == (
        'weary-turing · forked · "take the perf angle"')

    close = {"handle": "calm-hopper", "from_handle": "me"}
    assert aegis_glyph("aegis_close", close) == "✦"
    assert aegis_describe("aegis_close", close) == "calm-hopper · reaped"
    assert aegis_target("aegis_close", close) == Target("agent", "calm-hopper")
