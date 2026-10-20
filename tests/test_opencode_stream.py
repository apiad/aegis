import json
from pathlib import Path

import pytest

from aegis.claude.stream import (
    Delta,
    Echo,
    Init,
    Result,
    Step,
    Text,
    Title,
    ToolCall,
    ToolOutput,
)
from aegis.opencode.stream import DELTAS, STORED, Parser, tool_input, tool_name
from aegis.transcript.entries import Fold

FIX = Path(__file__).parent / "fixtures" / "opencode"


def lines(name: str) -> list[str]:
    """The fixture's lines a session keeps: the stored types and the deltas."""
    return [
        ln
        for ln in (FIX / f"{name}.jsonl").read_text().splitlines()
        if ln and json.loads(ln)["type"] in STORED | DELTAS
    ]


def first_prompt(name: str) -> str:
    for ln in lines(name):
        e = json.loads(ln)
        if e["type"] == "session.inbox.enqueued":
            return e["data"]["item"]["payload"]["text"]
    raise AssertionError("no prompt")


def fold(name: str, *sends: str, interrupt_before: str | None = None) -> Fold:
    """Fold a fixture the way a session stores it: spawn, the sends, then every
    stored line; deltas go through ``live`` and are not records."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="deep", model="m", cwd="/x")
    for s in sends or (first_prompt(name),):
        own("send", text=s)
    for ln in lines(name):
        if interrupt_before and interrupt_before in ln:
            own("interrupt")
            interrupt_before = None
        evs = f.parse("opencode", ln)
        if json.loads(ln)["type"] in DELTAS:
            f.live(evs)
            continue
        f.apply({"i": i, "ts": 1.0 + i, "src": "opencode", "line": ln}, evs)
        i += 1
    return f


def refold(name: str, *sends: str) -> list[dict]:
    """The same records, without the deltas: what a reload folds."""
    f, i = Fold(), 0
    recs = [
        {"src": "aegis", "kind": "spawn", "agent": "deep", "model": "m", "cwd": "/x"}
    ]
    recs += [
        {"src": "aegis", "kind": "send", "text": s}
        for s in sends or (first_prompt(name),)
    ]
    recs += [
        {"src": "opencode", "line": ln}
        for ln in lines(name)
        if json.loads(ln)["type"] not in DELTAS
    ]
    for r in recs:
        f.apply({"i": i, "ts": 1.0 + i, **r})
        i += 1
    return f.entries()


def kinds(f: Fold) -> list[tuple[str, str]]:
    return [(e["kind"], e["status"]) for e in f.entries()]


@pytest.mark.parametrize(
    "name", ["plain", "tool", "midturn", "edit", "task", "command"]
)
def test_live_deltas_end_where_a_reload_starts(name):
    sends = ("/hello the okapi",) if name == "command" else ()
    assert fold(name, *sends).entries() == refold(name, *sends)


def test_a_plain_turn():
    f = fold("plain")
    e = f.entries()
    assert e[1]["summary"].startswith("OpenCode ") and "opencode/" in e[1]["summary"]
    assert [k for k in kinds(f) if k[0] == "user"] == [("user", "ok")]
    assert any(x["kind"] == "prose" and x["md"].strip() for x in e)
    assert e[-1]["summary"].startswith("done in")  # a free model: no cost shown


def test_a_tool_call_is_a_bash_row_with_its_output():
    (t,) = [e for e in fold("tool").entries() if e["kind"] == "tool"]
    assert (t["title"], t["status"]) == ("Bash", "ok")
    assert "hi" in t["detail"]["tail"]


def test_a_prompt_sent_mid_turn_is_read_and_one_result_ends_both():
    f = fold("midturn", "Run `sleep 6` in the shell, then say A.", "Also say B.")
    assert [k for k in kinds(f) if k[0] == "user"] == [("user", "ok"), ("user", "ok")]
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 1


def test_an_interrupt_marks_the_call_interrupted_once():
    f = fold("abort", interrupt_before='"session.tool.failed"')
    tools = [e for e in f.entries() if e["kind"] == "tool"]
    assert [(t["status"], t["detail"]["result"]) for t in tools] == [
        ("err", "interrupted")
    ]
    assert [e["summary"] for e in f.entries() if e["kind"] == "error"] == [
        "interrupted"
    ]
    assert not any(e["summary"].startswith("done in") for e in f.entries())


def test_an_edit_has_its_diff():
    (t,) = [
        e
        for e in fold("edit").entries()
        if e["kind"] == "tool" and e["title"] == "Edit"
    ]
    lines = [ln for h in t["detail"]["diff"]["hunks"] for ln in h["lines"]]
    assert any(ln.startswith("-") for ln in lines) and any(
        ln.startswith("+") for ln in lines
    )


def test_a_subagent_counts_its_child_session_as_steps():
    (t,) = [e for e in fold("task").entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0


def test_a_command_shows_the_line_as_typed_with_its_template_under_it():
    f = fold("command", "/hello the okapi")
    (u,) = [e for e in f.entries() if e["kind"] == "user"]
    assert (u["status"], u["md"]) == ("ok", "/hello the okapi")
    assert "okapi" in u["detail"]["tail"] and u["detail"]["tail"] != u["md"]


def test_names_and_inputs_are_claudes():
    names = ("shell", "edit", "subagent", "aegis_monitor_start", "other_x")
    assert [tool_name(n) for n in names] == [
        "Bash", "Edit", "Task", "mcp__aegis__monitor_start", "other_x",
    ]  # fmt: skip
    assert tool_input("edit", {"path": "a", "oldString": "b", "replaceAll": True}) == {
        "file_path": "a", "old_string": "b", "replace_all": True,
    }  # fmt: skip
    assert tool_input("aegis_x", {"path": "a"}) == {"path": "a"}
    assert tool_input("subagent", {"agent": "explore"}) == {"subagent_type": "explore"}


def ev(type_, created=None, **data) -> str:
    out = {"type": type_, "data": data}
    if created is not None:
        out["created"] = created
    return json.dumps(out)


def test_the_parser_maps_one_turn():
    p = Parser()
    created = ev("session.created", sessionID="ses_1", version="2.0.28",
                 model={"id": "flash", "providerID": "go"}, location={"directory": "/r/x"})  # fmt: skip
    assert p.feed(created) == [
        Init(
            session_id="ses_1",
            model="go/flash",
            version="2.0.28",
            harness="OpenCode",
            cwd="/r/x",
        )  # fmt: skip
    ]
    user = ev("session.inbox.enqueued", sessionID="ses_1", inboxID="msg_u",
              item={"type": "user", "payload": {"text": "hi"}, "delivery": "steer"})  # fmt: skip
    assert p.feed(user) == [Echo(text="hi", expands=True)]
    assert p.feed(user) == []
    assert p.feed(ev("session.execution.started", 10, sessionID="ses_1")) == []
    a = {"sessionID": "ses_1", "assistantMessageID": "msg_a", "ordinal": 0}
    assert p.feed(ev("session.text.started", **a)) == [
        Text(text="", parent=None, usage=None, key="msg_a/prose/0")
    ]
    assert p.feed(ev("session.text.delta", **a, delta="Hel")) == [
        Delta(key="msg_a/prose/0", kind="prose", text="Hel")
    ]
    assert p.feed(
        ev("session.text.delta", sessionID="ses_1", assistantMessageID="x", delta="y")
    ) == [Delta(key="", kind="", text="")]
    call = {"sessionID": "ses_1", "assistantMessageID": "msg_a", "id": "c1"}
    assert p.feed(ev("session.tool.input.started", **call, name="read")) == []
    called = ev("session.tool.called", **call, input={"path": "/a"})
    assert p.feed(called) == [
        ToolCall(
            id="c1", name="Read", input={"file_path": "/a"}, parent=None, usage=None
        )
    ]
    assert p.feed(called) == []
    content = [{"type": "text", "text": "x"}]
    assert p.feed(ev("session.tool.success", **call, content=content)) == [
        ToolOutput(id="c1", text="x", is_error=False, parent=None)
    ]
    tokens = {
        "input": 100,
        "output": 20,
        "reasoning": 5,
        "cache": {"read": 3, "write": 2},
    }
    (st,) = p.feed(ev("session.step.ended", sessionID="ses_1", tokens=tokens, cost=0.5))
    assert isinstance(st, Step) and st.usage.context == 130
    assert p.feed(ev("session.renamed", sessionID="ses_1", title="Fix the parser")) == [
        Title(text="Fix the parser")
    ]
    (r,) = p.feed(ev("session.execution.succeeded", 1510, sessionID="ses_1"))
    assert isinstance(r, Result) and (r.is_error, r.duration_ms, r.cost_usd) == (
        False,
        1500,
        0.5,
    )
    assert p.feed(ev("session.execution.succeeded", sessionID="ses_other")) == []
    assert p.feed("not json")[0].raw == "not json"


def test_a_failed_turn_says_why():
    p = Parser()
    p.feed(ev("session.created", sessionID="ses_1"))
    err = {"type": "provider.no-route", "message": "Model unavailable: go/x"}
    (r,) = p.feed(ev("session.execution.failed", sessionID="ses_1", error=err))
    assert r.is_error and r.subtype == "provider.no-route: Model unavailable: go/x"


def test_a_model_switch_is_a_new_init():
    p = Parser()
    p.feed(
        ev("session.created", sessionID="ses_1", model={"id": "a", "providerID": "go"})
    )
    (i,) = p.feed(ev("session.model.selected", sessionID="ses_1",
                     model={"id": "b", "providerID": "go", "variant": "high"}))  # fmt: skip
    assert isinstance(i, Init) and i.model == "go/b"


def test_a_child_is_named_by_its_calls_progress():
    p = Parser()
    p.feed(ev("session.created", sessionID="ses_1"))
    for c in ("c1", "c2"):
        p.feed(
            ev("session.tool.input.started", sessionID="ses_1", id=c, name="subagent")
        )
    p.feed(ev("session.created", sessionID="ses_k", parentID="ses_1"))
    p.feed(ev("session.tool.progress", sessionID="ses_1", id="c2",
              metadata={"sessionID": "ses_k"}))  # fmt: skip
    p.feed(ev("session.tool.input.started", sessionID="ses_k", id="k1", name="shell"))
    (call,) = p.feed(ev("session.tool.called", sessionID="ses_k", id="k1", input={}))
    assert call.parent == "c2"


def test_every_delta_line_is_a_delta_even_one_nothing_draws():
    p = Parser()
    p.feed(ev("session.created", sessionID="ses_1"))
    p.feed(ev("session.created", sessionID="ses_k", parentID="ses_1"))
    for line in (
        ev(
            "session.text.delta",
            sessionID="ses_k",
            assistantMessageID="m",
            delta="child",
        ),
        ev("session.text.delta", sessionID="ses_1", assistantMessageID="?", delta="x"),
    ):
        (d,) = p.feed(line)
        assert isinstance(d, Delta) and not d.key
        assert Fold().live([d]) == []


def test_an_exit_mid_turn_does_not_leak_into_the_next_turn():
    f = Fold()
    rec = iter(range(100))

    def line(s):
        f.apply({"i": next(rec), "ts": 1.0, "src": "opencode", "line": s})

    line(ev("session.created", sessionID="ses_1"))
    line(ev("session.execution.started", 1000, sessionID="ses_1"))
    f.apply(
        {
            "i": next(rec),
            "ts": 2.0,
            "src": "aegis",
            "kind": "exit",
            "code": 1,
            "stderr_tail": [],
        }
    )
    line(ev("session.execution.started", 5000, sessionID="ses_1"))
    line(ev("session.execution.succeeded", 6000, sessionID="ses_1"))
    assert f.entries()[-1]["summary"].startswith("done in 1.0s")


def test_a_prompt_read_while_a_part_streams_sits_where_a_reload_puts_it():
    """The second prompt is read after the answer to the first opens and
    before it closes (#373): the answer sits above it, live and on a reload."""
    a = {"sessionID": "ses_1", "assistantMessageID": "msg_a", "ordinal": 0}
    b = {"sessionID": "ses_1", "assistantMessageID": "msg_b", "ordinal": 0}

    def user(iid, text):
        item = {"type": "user", "payload": {"text": text}, "delivery": "steer"}
        return ev("session.inbox.enqueued", sessionID="ses_1", inboxID=iid, item=item)

    stream = [
        ev("session.created", sessionID="ses_1", version="2.0.28",
           model={"id": "flash", "providerID": "go"}, location={"directory": "/r"}),
        user("msg_1", "one"),
        ev("session.execution.started", 10, sessionID="ses_1"),
        ev("session.text.started", **a),
        ev("session.text.delta", **a, delta="you said: "),
        user("msg_2", "two"),
        ev("session.text.delta", **a, delta="one"),
        ev("session.text.ended", **a, text="you said: one"),
        ev("session.text.started", **b),
        ev("session.text.delta", **b, delta="you said: two"),
        ev("session.text.ended", **b, text="you said: two"),
        ev("session.execution.succeeded", 20, sessionID="ses_1"),
    ]  # fmt: skip

    def folded(live: bool) -> Fold:
        f = Fold()
        recs = [
            {"src": "aegis", "kind": "spawn", "agent": "a", "model": "m", "cwd": "/r"}
        ]
        recs += [{"src": "aegis", "kind": "send", "text": t} for t in ("one", "two")]
        recs += [{"src": "opencode", "line": ln} for ln in stream]
        for i, r in enumerate(recs):
            if r["src"] == "opencode" and json.loads(r["line"])["type"] in DELTAS:
                if live:
                    f.live(f.parse("opencode", r["line"]))
                continue
            f.apply({"i": i, "ts": 1.0 + i, **r})
        return f

    live = folded(live=True).entries()
    assert live == folded(live=False).entries()
    said = [e["md"] for e in live if e["kind"] in ("user", "prose")]
    assert said == ["one", "you said: one", "two", "you said: two"]


def test_a_failed_turn_keeps_its_error_text():
    p = Parser()
    p.feed(ev("session.created", sessionID="ses_1"))
    err = {"type": "APIError", "message": "429 FreeUsageLimitError"}
    (r,) = p.feed(ev("session.execution.failed", sessionID="ses_1", error=err))
    assert r.error_text == "APIError: 429 FreeUsageLimitError"
