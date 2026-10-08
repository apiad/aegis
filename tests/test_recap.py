import json

from aegis.recap import (
    AWAY_S,
    SYSTEM,
    RecapOut,
    argv,
    load_recap,
    needed,
    parse,
    window,
)
from aegis.agent_ops import _render
from aegis.transcript.entries import EMPTY_STANDING


def prose(i, text):
    return {"id": f"e{i}.0", "kind": "prose", "md": text}


def user(i, text):
    return {"id": f"e{i}.0", "kind": "user", "md": text}


def test_needed_follows_the_thresholds():
    es = [user(1, "go"), prose(2, "a"), prose(3, "b")]
    now = 10_000.0
    assert not needed(es, set(), None, now)  # nothing unread
    assert not needed(es, {"e3.0"}, now - 60, now)  # one short, recent
    assert needed(es, {"e2.0", "e3.0"}, now - 60, now)  # two unread
    assert needed(es, {"e3.0"}, now - AWAY_S - 1, now)  # away long
    long = [user(1, "go"), prose(2, "word " * 301)]
    assert needed(long, {"e2.0"}, now - 60, now)  # one long


def test_window_starts_at_the_earlier_of_last_user_and_first_unread():
    es = [
        user(1, "first ask"),
        prose(2, "old reply"),
        user(3, "second ask"),
        prose(4, "new reply"),
    ]
    w = window(es, {"e2.0"}, EMPTY_STANDING)
    assert "first ask" not in w and "old reply" in w and "second ask" in w
    w2 = window(es, {"e4.0"}, EMPTY_STANDING)
    assert "old reply" not in w2 and "second ask" in w2 and "new reply" in w2


def test_window_carries_the_agents_report_and_plan_and_is_capped():
    st = {
        **EMPTY_STANDING,
        "report": {"attention": "needs_you", "line": "Merge or rebase?", "replies": []},
        "plan": [{"text": "land it", "state": "doing"}],
    }
    w = window([user(1, "go"), prose(2, "x" * 50_000)], {"e2.0"}, st)
    assert "Merge or rebase?" in w and "land it" in w
    assert "user: go" in w
    assert len(w) <= 13_000


def test_argv_sheds_tools_settings_and_mcp():
    a = argv("/bin/claude", "claude-haiku-4-5-20251001", "--- transcript ---")
    assert a[:2] == ["/bin/claude", "-p"]
    # The window opens on dashes, which the CLI reads as an option unless the
    # prompt comes last, after --.
    assert a[-2:] == ["--", "--- transcript ---"]
    for flag, value in [
        ("--model", "claude-haiku-4-5-20251001"),
        ("--output-format", "json"),
        ("--tools", ""),
        ("--setting-sources", ""),
        ("--mcp-config", '{"mcpServers": {}}'),
    ]:
        assert a[a.index(flag) + 1] == value
    assert "--strict-mcp-config" in a
    assert json.loads(a[a.index("--json-schema") + 1])["properties"].keys() == {
        "context",
        "ask",
    }
    assert a[a.index("--system-prompt") + 1]


def test_parse_reads_the_envelope_and_never_raises():
    out = json.dumps(
        {
            "result": '{"context": "fixing the archive", "ask": "merge or rebase?"}',
            "total_cost_usd": 0.004,
            "duration_ms": 1800,
        }
    )
    v, cost, ms = parse(out)
    assert (
        v == RecapOut(context="fixing the archive", ask="merge or rebase?")
        and cost == 0.004
        and ms == 1800
    )
    so = json.dumps(
        {
            "structured_output": {"context": "c", "ask": ""},
            "result": "",
            "total_cost_usd": 0.001,
            "duration_ms": 5,
        }
    )
    assert parse(so)[0] == RecapOut(context="c", ask="")
    assert parse("not json") == (None, 0.0, 0)
    assert parse(json.dumps({"result": "no object here"}))[0] is None
    bad = json.dumps(
        {
            "result": '{"context": "c", "ask": ""}',
            "total_cost_usd": "x",
            "duration_ms": "y",
        }
    )
    assert parse(bad) == (RecapOut(context="c", ask=""), 0.0, 0)


def test_load_recap_names_an_agent_or_says_what_is_wrong(tmp_path):
    assert load_recap(tmp_path) is None
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  haiku: {harness: claude-code, model: m, effort: low, permission: read}\n"
        "  oc: {harness: opencode, model: a/b, effort: low, permission: read}\n"
        "recap: {agent: haiku}\n"
    )
    assert load_recap(tmp_path) == {"agent": "haiku", "model": "m"}
    (tmp_path / ".aegis.yaml").write_text("agents: {}\nrecap: {agent: nope}\n")
    assert "nope" in load_recap(tmp_path)["error"]
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  oc: {harness: opencode, model: a/b, effort: low, permission: read}\nrecap: {agent: oc}\n"
    )
    assert "claude-code" in load_recap(tmp_path)["error"]
    (tmp_path / ".aegis.yaml").write_text("recap: {}\n")
    assert "agent" in load_recap(tmp_path)["error"]


def test_the_recap_never_invents_a_question():
    assert "never invent" in SYSTEM


def test_window_carries_the_turn_error_after_the_report():
    st = {
        **EMPTY_STANDING,
        "report": {"attention": "done", "line": "Shipped.", "replies": []},
        "turn_error": "claude exited with code 1",
    }
    w = window([user(1, "go"), prose(2, "ok")], {"e2.0"}, st)
    assert "ERROR: claude exited with code 1" in w
    assert w.index("AGENT REPORT") < w.index("ERROR:")
    assert "ERROR:" not in window([user(1, "go")], set(), EMPTY_STANDING)


def test_peer_read_never_shows_a_recap():
    e = {"kind": "recap", "summary": "fixing it", "detail": {}}
    assert _render(e, True) is None
