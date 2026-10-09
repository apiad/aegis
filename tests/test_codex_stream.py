import json
from pathlib import Path

import pytest

from aegis.claude.stream import Delta, Echo, Init, Result, Step, Text, ToolCall, Usage
from aegis.codex.stream import DELTAS, Parser, diff_pair
from aegis.transcript.entries import Fold

FIX = Path(__file__).parent / "fixtures" / "codex"


def lines(name: str) -> list[str]:
    return [ln for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln]


def first_prompt(name: str) -> str:
    for ln in lines(name):
        m = json.loads(ln)
        item = (m.get("params") or {}).get("item") or {}
        if m["method"] == "item/completed" and item.get("type") == "userMessage":
            return "".join(
                c.get("text", "") for c in item["content"] if c.get("type") == "text"
            )
    raise AssertionError("no prompt")


def fold(name: str, *sends: str, interrupt_before: str | None = None) -> Fold:
    """Fold a fixture the way a session stores it; deltas go through ``live``."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="cx", model="m", cwd="/x")
    for s in sends or (first_prompt(name),):
        own("send", text=s)
    for ln in lines(name):
        if interrupt_before and interrupt_before in ln:
            own("interrupt")
            interrupt_before = None
        evs = f.parse("codex", ln)
        if json.loads(ln)["method"] in DELTAS:
            f.live(evs)
            continue
        f.apply({"i": i, "ts": 1.0 + i, "src": "codex", "line": ln}, evs)
        i += 1
    return f


def refold(name: str, *sends: str) -> list[dict]:
    f, i = Fold(), 0
    recs = [{"src": "aegis", "kind": "spawn", "agent": "cx", "model": "m", "cwd": "/x"}]
    recs += [
        {"src": "aegis", "kind": "send", "text": s}
        for s in sends or (first_prompt(name),)
    ]
    recs += [{"src": "codex", "line": ln} for ln in lines(name)
             if json.loads(ln)["method"] not in DELTAS]  # fmt: skip
    for r in recs:
        f.apply({"i": i, "ts": 1.0 + i, **r})
        i += 1
    return f.entries()


def tools(f: Fold) -> list[dict]:
    return [e for e in f.entries() if e["kind"] == "tool"]


@pytest.mark.parametrize("name", ["plain", "tool", "mcp", "steer", "subagent"])
def test_live_deltas_end_where_a_reload_starts(name):
    assert fold(name).entries() == refold(name)


def test_a_plain_turn_names_codex_its_version_and_model():
    e = fold("plain").entries()
    assert any(x["summary"].startswith("Codex 0.162.1 · openrouter/") for x in e)
    assert [x["status"] for x in e if x["kind"] == "user"] == ["ok"]
    assert any(x["kind"] == "prose" and x["md"].strip() for x in e)
    assert e[-1]["summary"].startswith("done in")


def test_a_shell_call_is_a_bash_row_with_its_output():
    (t,) = tools(fold("tool"))
    assert (t["title"], t["status"]) == ("Bash", "ok")
    assert "hi" in t["detail"]["tail"]


def test_an_mcp_call_is_named_as_claude_names_it():
    (t,) = tools(fold("mcp"))
    assert t["status"] == "ok" and "tok-record" in t["detail"]["tail"]


def test_a_steered_prompt_is_read_in_the_same_turn():
    f = fold("steer", first_prompt("steer"), "Also say the word MANGO.")
    assert [e["status"] for e in f.entries() if e["kind"] == "user"] == ["ok", "ok"]
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 1


def test_an_interrupt_ends_the_turn_once_and_marks_the_call():
    f = fold("interrupt", interrupt_before='"turn/completed"')
    assert [t["status"] for t in tools(f)] == ["err"]
    assert [e["summary"] for e in f.entries() if e["kind"] == "error"] == [
        "interrupted"
    ]


def test_a_subagent_is_a_task_whose_child_thread_counts_as_steps():
    (t,) = [e for e in fold("subagent").entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0


def test_a_compaction_is_shown_once():
    f = fold("compact", first_prompt("compact"), "/compact")
    assert sum(e["kind"] == "user" and e["md"] == "/compact" for e in f.entries()) == 1
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 2


def test_a_patch_update_is_an_edit_with_its_diff():
    thread = {
        "method": "aegis/thread",
        "params": {"thread": {"id": "t1", "model": "m", "modelProvider": "p"}},
    }
    change = {
        "path": "/x/a.txt",
        "kind": {"type": "update"},
        "diff": "@@ -1 +1 @@\n-hello\n+bye\n",
    }
    item = {
        "type": "fileChange",
        "id": "fc1",
        "status": "completed",
        "changes": [change],
    }
    f = Fold()
    f.apply(
        {
            "i": 0,
            "ts": 1.0,
            "src": "aegis",
            "kind": "spawn",
            "agent": "cx",
            "model": "m",
            "cwd": "/x",
        }
    )
    for i, line in enumerate([thread,
                              {"method": "turn/started", "params": {"threadId": "t1", "turn": {"id": "u1"}}},
                              {"method": "item/completed", "params": {"threadId": "t1", "turnId": "u1", "item": item}}],
                             start=1):  # fmt: skip
        ln = json.dumps(line)
        f.apply(
            {"i": i, "ts": 1.0 + i, "src": "codex", "line": ln}, f.parse("codex", ln)
        )
    (t,) = tools(f)
    assert (
        t["title"] == "Edit"
        and t["detail"]["diff"]["removed"]
        and t["detail"]["diff"]["added"]
    )


def test_diff_pair_splits_a_unified_diff():
    assert diff_pair("@@ -1,2 +1,2 @@\n keep\n-old\n+new\n") == (
        "keep\nold",
        "keep\nnew",
    )


def line(method: str, **params) -> str:
    return json.dumps({"method": method, "params": params})


def test_the_parser_maps_one_turn():
    p = Parser()
    p.feed(line("aegis/initialize", userAgent="aegis/0.162.1 (Ubuntu; x86_64)"))
    # A model no price table will ever hold, so Task 6's prices leave this test alone.
    assert p.feed(
        line(
            "aegis/thread",
            thread={"id": "t1", "model": "gpt-test", "modelProvider": "openai"},
        )
    ) == [
        Init(
            session_id="t1", model="openai/gpt-test", version="0.162.1", harness="Codex"
        )
    ]
    assert p.feed(line("turn/started", threadId="t1", turn={"id": "u1"})) == []
    user = {
        "type": "userMessage",
        "id": "m0",
        "content": [{"type": "text", "text": "hi"}],
    }
    assert p.feed(line("item/completed", threadId="t1", turnId="u1", item=user)) == [
        Echo(text="hi")
    ]
    msg = {"type": "agentMessage", "id": "a1", "text": ""}
    assert p.feed(line("item/started", threadId="t1", turnId="u1", item=msg)) == [
        Text(text="", parent=None, usage=None, key="a1")
    ]
    assert p.feed(
        line(
            "item/agentMessage/delta",
            threadId="t1",
            turnId="u1",
            itemId="a1",
            delta="he",
        )
    ) == [Delta(key="a1", kind="prose", text="he")]
    usage = {"last": {"inputTokens": 1000, "cachedInputTokens": 400, "cacheWriteInputTokens": 0,
                      "outputTokens": 30, "reasoningOutputTokens": 10, "totalTokens": 1030},
             "total": {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 30,
                       "reasoningOutputTokens": 10, "totalTokens": 1030},
             "modelContextWindow": 258400}  # fmt: skip
    assert p.feed(
        line("thread/tokenUsage/updated", threadId="t1", turnId="u1", tokenUsage=usage)
    ) == [
        Step(
            usage=Usage(input=600, cache_creation=0, cache_read=400, output=30),
            parent=None,
        )
    ]
    done = {"id": "u1", "status": "completed", "durationMs": 1200, "error": None}
    assert p.feed(line("turn/completed", threadId="t1", turn=done)) == [
        Result(
            is_error=False,
            subtype="success",
            duration_ms=1200,
            cost_usd=None,
            stop_reason=None,
            context_window=258400,
        )  # fmt: skip
    ]


def test_a_turn_of_a_thread_nobody_spawned_is_not_ours():
    p = Parser()
    p.feed(
        line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"})
    )
    msg = {"type": "agentMessage", "id": "a9", "text": "x"}
    assert p.feed(line("item/completed", threadId="other", turnId="u", item=msg)) == []


def test_a_command_line_is_echoed_once_and_its_user_message_is_not():
    p = Parser()
    p.feed(
        line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"})
    )
    assert p.feed(line("aegis/command", line="/review look at x", kind="review")) == [
        Echo(text="/review look at x")
    ]
    user = {
        "type": "userMessage",
        "id": "m1",
        "content": [{"type": "text", "text": "look at x"}],
    }
    assert p.feed(line("item/completed", threadId="t1", turnId="u", item=user)) == []


def test_a_shell_call_shows_the_command_codex_ran_not_its_wrapper():
    p = Parser()
    p.feed(
        line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"})
    )
    item = {"type": "commandExecution", "id": "c1", "command": "/bin/bash -lc 'ls -la'",
            "commandActions": [{"type": "unknown", "command": "ls -la"}], "status": "inProgress"}  # fmt: skip
    assert p.feed(line("item/started", threadId="t1", turnId="u", item=item)) == [
        ToolCall(
            id="c1", name="Bash", input={"command": "ls -la"}, parent=None, usage=None
        )
    ]


def test_a_priced_model_costs_its_requests_and_a_free_one_costs_nothing():
    from aegis.usage.prices import codex_prices_for

    assert (
        codex_prices_for("openrouter/nvidia/x:free").cost(
            inp=10**6, out=10**6, cc5=0, cc1=0, cache_read=0
        )
        == 0
    )
    assert codex_prices_for("openrouter/paid/x") is None
    assert codex_prices_for("ollama/qwen") is None
    p = Parser()
    p.feed(
        line(
            "aegis/thread",
            thread={"id": "t1", "model": "gpt-6.1-sol", "modelProvider": "openai"},
        )
    )
    p.feed(line("turn/started", threadId="t1", turn={"id": "u1"}))
    last = {
        "inputTokens": 1_000_000,
        "cachedInputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 1_000_000,
    }
    p.feed(
        line(
            "thread/tokenUsage/updated",
            threadId="t1",
            turnId="u1",
            tokenUsage={"last": last, "total": last},
        )
    )
    (r,) = p.feed(
        line("turn/completed", threadId="t1", turn={"id": "u1", "status": "completed"})
    )
    assert r.cost_usd == pytest.approx(
        float(codex_prices_for("openai/gpt-6.1-sol").input)
    )


def test_the_aegis_servers_tools_are_named_as_claude_names_them():
    from aegis.codex.config import SERVER

    p = Parser()
    p.feed(
        line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"})
    )
    item = {
        "type": "mcpToolCall",
        "id": "c1",
        "server": SERVER,
        "tool": "meta",
        "arguments": {},
    }
    (call,) = p.feed(line("item/started", threadId="t1", turnId="u", item=item))
    assert call.name == "mcp__aegis__meta"
