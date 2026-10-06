"""The pure descriptor registry: name normalisation, glyphs, lines, targets."""
from __future__ import annotations

import pytest

from aegis.comms.descriptors import (
    ADMIN, CONVERSATION, COORDINATION, INTROSPECTION, PALE_GLYPH, Target,
    aegis_describe, aegis_family, aegis_glyph, aegis_target, descriptor_for,
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


def test_canvas_writes_name_the_section_and_count_the_lines():
    write = {"name": "report", "section": "Findings",
             "content": "a\nb\nc", "from_handle": "me"}
    assert aegis_glyph("aegis_canvas_write_section", write) == "▤"
    assert aegis_describe("aegis_canvas_write_section", write) == (
        "report §Findings · 3 lines")
    assert aegis_target("aegis_canvas_write_section", write) == Target(
        "canvas", "report")

    append = {"name": "report", "section": "Log", "text": "x\ny"}
    assert aegis_describe("aegis_canvas_append_to_section", append) == (
        "report §Log · +2 lines")


def test_canvas_attach_and_detach_take_opposite_glyphs():
    assert aegis_glyph("aegis_canvas_open", {"name": "report"}) == "▥"
    assert aegis_glyph("aegis_canvas_subscribe", {"name": "report"}) == "▥"
    assert aegis_glyph("aegis_canvas_unsubscribe", {"name": "report"}) == "▧"
    assert aegis_describe("aegis_canvas_subscribe", {"name": "report"}) == (
        "report · all sections")
    assert aegis_describe(
        "aegis_canvas_subscribe",
        {"name": "report", "sections": ["Findings", "Log"]}) == (
        "report · Findings, Log")


def test_terminal_verbs_name_the_terminal():
    run = {"name": "build", "cmd": "pytest -q", "from_handle": "me"}
    assert aegis_glyph("aegis_term_run", run) == "■"
    assert aegis_describe("aegis_term_run", run) == 'build · "pytest -q"'
    assert aegis_target("aegis_term_run", run) == Target("term", "build")

    assert aegis_glyph("aegis_term_spawn", {"name": "build"}) == "▥"
    assert aegis_glyph("aegis_term_close", {"name": "build"}) == "▧"
    assert aegis_describe("aegis_term_close", {"name": "build"}) == (
        "build · closed")


def test_term_keys_renders_control_bytes_readably():
    assert aegis_describe("aegis_term_keys",
                          {"name": "build", "keys": "\x03"}) == "build · ^C"
    assert aegis_describe("aegis_term_keys",
                          {"name": "build", "keys": "y\n"}) == 'build · "y⏎"'


def test_group_broadcast_and_waits_point_at_the_group():
    bc = {"from_handle": "me", "group": "reviewers",
          "objective": "review your section"}
    assert aegis_glyph("aegis_group_broadcast", bc) == "⁂"
    assert aegis_describe("aegis_group_broadcast", bc) == (
        'reviewers · "review your section"')
    assert aegis_target("aegis_group_broadcast", bc) == Target(
        "group", "reviewers")

    assert aegis_glyph("aegis_group_wait_all", {"group": "reviewers"}) == "⁑"
    assert aegis_describe("aegis_group_wait_all",
                          {"group": "reviewers", "reducer": "concat"}) == (
        "reviewers · all · concat")
    assert aegis_describe("aegis_group_wait_any",
                          {"group": "reviewers"}) == "reviewers · any"


def test_group_membership_verbs_take_the_reshape_glyph():
    assert aegis_glyph("aegis_group_spawn",
                       {"profile": "main", "group": "reviewers"}) == "✧"
    assert aegis_describe("aegis_group_spawn",
                          {"profile": "main", "group": "reviewers"}) == (
        "reviewers · main")
    assert aegis_describe("aegis_group_spawn_mixed",
                          {"group": "reviewers",
                           "profiles": ["main", "fast"]}) == (
        "reviewers · main, fast")
    assert aegis_glyph("aegis_group_rename", {"old": "a", "new": "b"}) == "⌗"
    assert aegis_describe("aegis_group_rename",
                          {"old": "a", "new": "b"}) == "a · renamed to b"
    assert aegis_describe("aegis_group_dissolve",
                          {"group": "reviewers"}) == "reviewers · dissolved"
    assert aegis_describe("aegis_group_move_member",
                          {"handle": "calm-hopper", "from_group": "a",
                           "to_group": "b"}) == "calm-hopper · a to b"


def test_claim_and_release_are_opposite_circles():
    claim = {"paths": ["src/aegis/mcp/", "src/aegis/comms/"],
             "from_handle": "me", "intent": "exclusive"}
    assert aegis_glyph("aegis_claim", claim) == "⊙"
    assert aegis_describe("aegis_claim", claim) == (
        "exclusive · src/aegis/mcp/ · 2 paths")
    assert aegis_target("aegis_claim", claim) == Target(
        "path", "src/aegis/mcp/")
    assert aegis_family("aegis_claim") == COORDINATION

    rel = {"claim_id": "01K4TZ", "from_handle": "me"}
    assert aegis_glyph("aegis_release", rel) == "⊚"
    assert aegis_describe("aegis_release", rel) == "01K4TZ"
    assert aegis_target("aegis_release", rel) == Target("claim", "01K4TZ")


def test_a_single_path_claim_does_not_say_one_paths():
    claim = {"paths": ["src/aegis/mcp/"], "intent": "shared"}
    assert aegis_describe("aegis_claim", claim) == "shared · src/aegis/mcp/"


def test_wakers_arm_and_disarm():
    mon = {"from_handle": "me", "description": "pytest", "done": "test -f ok"}
    assert aegis_glyph("aegis_monitor", mon) == "◷"
    assert aegis_describe("aegis_monitor", mon) == "pytest"
    assert aegis_target("aegis_monitor", mon) == Target("self", "me")

    rem = {"from_handle": "me", "note": "check the tag", "after": "20m"}
    assert aegis_glyph("aegis_remind", rem) == "◷"
    assert aegis_describe("aegis_remind", rem) == 'in 20m · "check the tag"'
    assert aegis_describe("aegis_remind",
                          {"note": "check the tag"}) == (
        'at turn end · "check the tag"')

    assert aegis_glyph("aegis_monitor_cancel", {"monitor_id": "m1"}) == "◶"
    assert aegis_describe("aegis_monitor_cancel", {"monitor_id": "m1"}) == "m1"
    assert aegis_glyph("aegis_reminder_cancel", {"reminder_id": "r1"}) == "◶"


def test_loop_stop_and_self_naming():
    stop = {"from_handle": "me", "reason": "wired end to end"}
    assert aegis_glyph("aegis_loop_stop", stop) == "◼"
    assert aegis_describe("aegis_loop_stop", stop) == '"wired end to end"'

    ren = {"old_handle": "civic-cook", "new_handle": "aegis-call-format",
           "title": "design the call format"}
    assert aegis_glyph("aegis_rename", ren) == "❖"
    assert aegis_describe("aegis_rename", ren) == (
        'aegis-call-format · "design the call format"')
    assert aegis_target("aegis_rename", ren) == Target(
        "agent", "aegis-call-format")

    tit = {"from_handle": "me", "title": "fix the eviction race"}
    assert aegis_glyph("aegis_title", tit) == "❖"
    assert aegis_describe("aegis_title", tit) == '"fix the eviction race"'


def test_the_pale_tier_shares_one_glyph_and_describes_the_call():
    assert aegis_glyph("aegis_list_sessions", {}) == PALE_GLYPH
    assert aegis_describe("aegis_list_sessions", {}) == "list sessions"
    assert aegis_family("aegis_list_sessions") == INTROSPECTION

    assert aegis_describe("aegis_read_peer", {"handle": "weary-turing"}) == (
        "read peer · weary-turing")
    assert aegis_describe("aegis_config_show", {}) == "config show"
    assert aegis_family("aegis_config_show") == ADMIN
    assert aegis_target("aegis_list_sessions", {}) is None
