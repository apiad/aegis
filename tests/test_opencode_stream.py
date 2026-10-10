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
from aegis.opencode.stream import DELTA, Parser, tool_input, tool_name
from aegis.transcript.entries import Fold

FIX = Path(__file__).parent / "fixtures" / "opencode"


def lines(name: str) -> list[str]:
    return [ln for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln]


def first_prompt(name: str) -> str:
    for ln in lines(name):
        e = json.loads(ln)
        part = e["properties"].get("part") or {}
        if e["type"] == "message.part.updated" and part.get("type") == "text":
            return part["text"]
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
        if json.loads(ln)["type"] == DELTA:
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
        if json.loads(ln)["type"] != DELTA
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
    assert e[1]["summary"].startswith("OpenCode ") and "opencode-go/" in e[1]["summary"]
    assert [k for k in kinds(f) if k[0] == "user"] == [("user", "ok")]
    assert any(x["kind"] == "prose" and x["md"].strip() for x in e)
    assert e[-1]["summary"].startswith("done in") and "$" in e[-1]["summary"]


def test_a_tool_call_is_a_bash_row_with_its_output():
    (t,) = [e for e in fold("tool").entries() if e["kind"] == "tool"]
    assert (t["title"], t["status"]) == ("Bash", "ok")
    assert "hi" in t["detail"]["tail"]


def test_a_prompt_sent_mid_turn_is_read_and_one_result_ends_both():
    f = fold("midturn", "Run `sleep 6` in bash, then say A.", "Also say B.")
    assert [k for k in kinds(f) if k[0] == "user"] == [("user", "ok"), ("user", "ok")]
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 1


def test_an_abort_is_interrupted_once_and_its_late_tool_part_changes_nothing():
    f = fold("abort", interrupt_before='"session.error"')
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
    assert t["detail"]["diff"]["removed"] and t["detail"]["diff"]["added"]


def test_a_task_counts_its_child_session_as_steps():
    (t,) = [e for e in fold("task").entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0


def test_a_command_shows_the_line_as_typed_with_its_template_under_it():
    f = fold("command", "/hello the okapi")
    (u,) = [e for e in f.entries() if e["kind"] == "user"]
    assert (u["status"], u["md"]) == ("ok", "/hello the okapi")
    assert "okapi" in u["detail"]["tail"] and u["detail"]["tail"] != u["md"]


def test_names_and_inputs_are_claudes():
    assert [tool_name(n) for n in ("bash", "edit", "aegis_monitor_start", "other_x")] == [
        "Bash", "Edit", "mcp__aegis__monitor_start", "other_x",
    ]  # fmt: skip
    assert tool_input({"filePath": "a", "oldString": "b", "replaceAll": True}) == {
        "file_path": "a", "old_string": "b", "replace_all": True,
    }  # fmt: skip


def ev(type_, **props) -> str:
    return json.dumps({"type": type_, "properties": props})


def test_the_parser_maps_one_turn():
    p = Parser()
    info = {"id": "ses_1", "title": "New session - x", "version": "1.18.31",
            "model": {"id": "flash", "providerID": "go"}}  # fmt: skip
    assert p.feed(ev("session.created", sessionID="ses_1", info=info)) == [
        Init(
            session_id="ses_1", model="go/flash", version="1.18.31", harness="OpenCode"
        )
    ]
    user = {"id": "msg_u", "role": "user", "sessionID": "ses_1", "time": {"created": 1}}
    assert p.feed(ev("message.updated", sessionID="ses_1", info=user)) == []
    part = {
        "id": "prt_u",
        "messageID": "msg_u",
        "sessionID": "ses_1",
        "type": "text",
        "text": "hi",
    }
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=part)) == [
        Echo(text="hi", expands=True)
    ]
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=part)) == []
    synth = {**part, "id": "prt_s", "synthetic": True}
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=synth)) == []
    asst = {
        "id": "msg_a",
        "role": "assistant",
        "sessionID": "ses_1",
        "time": {"created": 10},
    }
    p.feed(ev("message.updated", sessionID="ses_1", info=asst))
    opened = {
        "id": "prt_t",
        "messageID": "msg_a",
        "sessionID": "ses_1",
        "type": "text",
        "text": "",
    }
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=opened)) == [
        Text(text="", parent=None, usage=None, key="prt_t")
    ]
    assert p.feed(ev(DELTA, sessionID="ses_1", messageID="msg_a", partID="prt_t",
                     field="text", delta="Hel")) == [Delta(key="prt_t", kind="prose", text="Hel")]  # fmt: skip
    assert p.feed(
        ev(DELTA, sessionID="ses_1", partID="prt_nope", field="text", delta="x")
    ) == [Delta(key="", kind="", text="")]
    running = {"id": "prt_c", "messageID": "msg_a", "sessionID": "ses_1", "type": "tool",
               "tool": "read", "callID": "c1", "state": {"status": "running", "input": {"filePath": "/a"}}}  # fmt: skip
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=running)) == [
        ToolCall(
            id="c1", name="Read", input={"file_path": "/a"}, parent=None, usage=None
        )
    ]
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=running)) == []
    done = {
        **running,
        "state": {"status": "completed", "input": {"filePath": "/a"}, "output": "x"},
    }
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=done)) == [
        ToolOutput(id="c1", text="x", is_error=False, parent=None)
    ]
    step = {"id": "prt_f", "messageID": "msg_a", "sessionID": "ses_1", "type": "step-finish",
            "tokens": {"total": 130, "input": 100, "output": 20, "reasoning": 5,
                       "cache": {"read": 3, "write": 2}}, "cost": 0.5}  # fmt: skip
    (st,) = p.feed(ev("message.part.updated", sessionID="ses_1", part=step))
    assert isinstance(st, Step) and st.usage.context == 130
    asst_done = {**asst, "cost": 0.5, "time": {"created": 10, "completed": 1510}}
    p.feed(ev("message.updated", sessionID="ses_1", info=asst_done))
    titled = {**info, "title": "Fix the parser"}
    assert p.feed(ev("session.updated", sessionID="ses_1", info=titled)) == [
        Title(text="Fix the parser")
    ]
    (r,) = p.feed(ev("session.idle", sessionID="ses_1"))
    assert isinstance(r, Result) and (r.is_error, r.duration_ms, r.cost_usd) == (
        False,
        1500,
        0.5,
    )
    assert p.feed(ev("session.idle", sessionID="ses_1")) == []
    assert p.feed(ev("session.idle", sessionID="ses_other")) == []
    assert p.feed("not json")[0].raw == "not json"


def _turn_start(p: Parser, mid: str, created: int) -> None:
    info = {
        "id": mid,
        "role": "assistant",
        "sessionID": "ses_1",
        "time": {"created": created},
    }
    p.feed(ev("message.updated", sessionID="ses_1", info=info))


def test_every_delta_line_is_a_delta_even_one_nothing_draws():
    p = Parser()
    p.feed(ev("session.created", sessionID="ses_1", info={"id": "ses_1"}))
    p.feed(
        ev(
            "session.created",
            sessionID="ses_k",
            info={"id": "ses_k", "parentID": "ses_1"},
        )
    )
    for line in (
        ev(DELTA, sessionID="ses_k", partID="prt_k", field="text", delta="child"),
        ev(DELTA, sessionID="ses_1", partID="prt_unknown", field="text", delta="x"),
    ):
        (d,) = p.feed(line)
        assert isinstance(d, Delta) and not d.key
        assert Fold().live([d]) == []


def test_an_exit_mid_turn_does_not_leak_into_the_next_turn():
    f = Fold()
    rec = iter(range(100))

    def line(s):
        f.apply({"i": next(rec), "ts": 1.0, "src": "opencode", "line": s})

    line(ev("session.created", sessionID="ses_1", info={"id": "ses_1"}))
    line(ev("message.updated", sessionID="ses_1",
            info={"id": "m1", "role": "assistant", "sessionID": "ses_1", "time": {"created": 1000}}))  # fmt: skip
    line(ev("session.error", sessionID="ses_1", error={"name": "APIError"}))
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
    line(ev("message.updated", sessionID="ses_1",
            info={"id": "m2", "role": "assistant", "sessionID": "ses_1",
                  "time": {"created": 5000, "completed": 6000}}))  # fmt: skip
    line(ev("session.idle", sessionID="ses_1"))
    assert f.entries()[-1]["summary"].startswith("done in 1.0s")


def test_a_late_text_part_of_an_aborted_message_changes_nothing():
    p = Parser()
    p.feed(ev("session.created", sessionID="ses_1", info={"id": "ses_1"}))
    _turn_start(p, "m1", 10)
    opened = {
        "id": "prt_t",
        "messageID": "m1",
        "sessionID": "ses_1",
        "type": "text",
        "text": "",
    }
    p.feed(ev("message.part.updated", sessionID="ses_1", part=opened))
    p.feed(
        ev("session.error", sessionID="ses_1", error={"name": "MessageAbortedError"})
    )
    assert len(p.feed(ev("session.idle", sessionID="ses_1"))) == 1
    closed = {**opened, "text": "half"}
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=closed)) == []


def test_the_session_names_its_working_directory():
    p = Parser()
    info = {"id": "ses_1", "directory": "/r/x", "version": "1",
            "model": {"id": "flash", "providerID": "go"}}  # fmt: skip
    (init,) = p.feed(ev("session.created", sessionID="ses_1", info=info))
    assert isinstance(init, Init) and init.cwd == "/r/x"
