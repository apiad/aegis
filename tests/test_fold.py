import json

from aegis.transcript.entries import Fold, fold_records


class Rec:
    """Builds store records the way a session writes them."""

    def __init__(self):
        self.records = []

    def _add(self, **kw):
        r = {"i": len(self.records), "ts": 1000.0 + len(self.records), **kw}
        self.records.append(r)
        return r

    def own(self, kind, **kw):
        return self._add(src="aegis", kind=kind, **kw)

    def claude(self, obj):
        return self._add(src="claude", line=json.dumps(obj))

    def echo(self, text):
        return self.claude(
            {
                "type": "user",
                "isReplay": True,
                "message": {"role": "user", "content": text},
            }
        )

    def text(self, text, parent=None):
        return self.claude(
            {
                "type": "assistant",
                "parent_tool_use_id": parent,
                "message": {"content": [{"type": "text", "text": text}]},
            }
        )

    def call(self, id, name, inp, parent=None):
        return self.claude(
            {
                "type": "assistant",
                "parent_tool_use_id": parent,
                "message": {
                    "content": [
                        {"type": "tool_use", "id": id, "name": name, "input": inp}
                    ]
                },
            }
        )

    def output(self, id, text, is_error=False):
        return self.claude(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": id,
                            "content": text,
                            "is_error": is_error,
                        }
                    ]
                },
            }
        )

    def result(self, cost=0.01, is_error=False, subtype="success"):
        return self.claude(
            {
                "type": "result",
                "subtype": subtype,
                "is_error": is_error,
                "duration_ms": 1500,
                "total_cost_usd": cost,
            }
        )


def run(rec: Rec) -> tuple[Fold, list[list[dict]]]:
    f = Fold()
    return f, [f.apply(r) for r in rec.records]


def test_the_echo_creates_the_user_entry_not_the_send():
    r = Rec()
    r.own("send", text="hello")
    r.echo("hello")
    f, ops = run(r)
    (pending,) = ops[0]
    assert (pending["upsert"]["id"], pending["upsert"]["status"]) == (
        "pending:0",
        "pending",
    )
    assert ops[1][0] == {"remove": "pending:0"}
    (user,) = f.entries()
    assert (user["id"], user["kind"], user["status"], user["md"]) == (
        "e1.0",
        "user",
        "ok",
        "hello",
    )


def test_two_sends_before_one_echo_keep_their_order():
    r = Rec()
    r.own("send", text="first")
    r.call("t1", "Bash", {"command": "sleep 6"})
    r.own("send", text="second")
    r.echo("first")
    r.output("t1", "done")
    r.echo("second")
    r.result()
    f, ops = run(r)
    assert ops[3][0] == {"remove": "pending:0"}
    assert ops[5][0] == {"remove": "pending:2"}
    users = [e["md"] for e in f.entries() if e["kind"] == "user"]
    assert users == ["first", "second"]
    assert not [e for e in f.entries() if e["status"] == "pending"]


def test_system_notices_make_no_entry():
    r = Rec()
    for sub in ("hook_started", "thinking_tokens", "task_notification"):
        r.claude({"type": "system", "subtype": sub})
    f, ops = run(r)
    assert ops == [[], [], []] and f.entries() == []


def test_tool_output_replaces_its_call_in_place():
    r = Rec()
    r.call("t1", "Bash", {"command": "pytest -q", "description": "Run tests"})
    r.output("t1", "....\n3 passed")
    f, ops = run(r)
    running = ops[0][0]["upsert"]
    assert (running["id"], running["status"], running["summary"]) == (
        "t1",
        "running",
        "Run tests",
    )
    (done,) = f.entries()
    assert (done["id"], done["status"], done["detail"]["result"]) == (
        "t1",
        "ok",
        "3 passed",
    )
    assert done["detail"]["collapsed"] is True


def test_a_failure_starts_open_with_its_tail():
    r = Rec()
    r.call("t1", "Bash", {"command": "mmdc"})
    r.output("t1", "Exit code 1\nError: Parse error on line 9", is_error=True)
    f, _ = run(r)
    (e,) = f.entries()
    assert e["status"] == "err" and e["detail"]["collapsed"] is False
    assert e["detail"]["result"] == "Exit code 1 · Error: Parse error on line 9"
    assert "Parse error" in e["detail"]["tail"]


def test_an_edit_carries_its_diff_window():
    r = Rec()
    r.call(
        "t1",
        "Edit",
        {
            "file_path": "/x/a.py",
            "old_string": "a = 1\nb = 2",
            "new_string": "a = 1\nb = 3",
        },
    )
    r.output("t1", "The file /x/a.py has been updated.")
    f, _ = run(r)
    (e,) = f.entries()
    assert e["glyph"] == "✎" and e["summary"] == "edit a.py"
    assert e["detail"]["diff"] == {
        "path": "/x/a.py",
        "removed": ["b = 2"],
        "added": ["b = 3"],
        "elided": 0,
    }
    assert e["detail"]["result"] == "+1 −1"


def test_subagent_events_count_as_steps_on_their_task():
    r = Rec()
    r.call("task1", "Task", {"description": "explore"})
    r.call("inner1", "Read", {"file_path": "/x"}, parent="task1")
    r.text("inner prose", parent="task1")
    f, _ = run(r)
    (task,) = f.entries()
    assert task["detail"]["steps"] == 2 and task["summary"] == "subagent: explore"


def test_result_reports_the_turns_own_cost():
    r = Rec()
    r.result(cost=0.05)
    r.result(cost=0.08)
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == [
        "done in 1.5s · $0.05",
        "done in 1.5s · $0.03",
    ]


def test_an_error_result_after_an_interrupt_reads_interrupted():
    r = Rec()
    r.own("interrupt")
    r.result(is_error=True, subtype="error_during_execution")
    f, _ = run(r)
    (e,) = f.entries()
    assert e["kind"] == "error" and e["summary"].startswith("interrupted")


def test_garbled_line_is_shown_not_fatal():
    r = Rec()
    r._add(src="claude", line="Segmentation fault")
    r.text("still alive")
    f, _ = run(r)
    assert [e["kind"] for e in f.entries()] == ["system", "prose"]


def test_refolding_the_same_records_gives_identical_entries():
    r = Rec()
    r.own("spawn", profile="opus", model="opus", cwd="/x")
    r.own("send", text="go")
    r.claude(
        {
            "type": "system",
            "subtype": "init",
            "model": "m",
            "claude_code_version": "2.1.283",
        }
    )
    r.echo("go")
    r.claude(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": "ok"},
                ]
            },
        }
    )
    r.call("t1", "Read", {"file_path": "/x/a"})
    r.output("t1", "1\n2\n3")
    r.result()
    live, _ = run(r)
    assert fold_records(r.records).entries() == live.entries()
    assert fold_records(r.records).entries() == fold_records(r.records).entries()


def test_a_call_still_running_when_the_turn_ends_is_marked():
    r = Rec()
    r.call("t1", "Bash", {"command": "sleep 60"})
    r.own("interrupt")
    r.result(is_error=True, subtype="error_during_execution")
    f, _ = run(r)
    tool = f.entries()[0]
    assert (tool["status"], tool["detail"]["result"]) == ("err", "interrupted")


def test_a_turn_that_ends_normally_with_a_call_unanswered_publishes_its_update():
    r = Rec()
    r.call("t1", "Bash", {"command": "x"})
    r.result()
    f, ops = run(r)
    assert [op["upsert"]["id"] for op in ops[1]] == ["t1", "e1.0"]
    assert f.entries()[0]["detail"]["result"] == "no result"


def test_stopping_ends_running_calls_and_loses_unread_prompts():
    r = Rec()
    r.own("send", text="first")
    r.echo("first")
    r.call("t1", "Bash", {"command": "sleep 60"})
    r.own("send", text="never read")
    r.own("stop")
    f, _ = run(r)
    by_kind = {(e["kind"], e["status"]) for e in f.entries()}
    assert ("user", "lost") in by_kind and ("tool", "err") in by_kind
    assert f.entries()[-1]["summary"] == "stopped"


def test_resume_and_server_stopped_lines():
    r = Rec()
    r.own("server_stopped")
    r.own("resume", claude_session_id="cs")
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == [
        "the server stopped during a turn",
        "resumed",
    ]


def test_activity_prefers_a_running_call_then_the_latest_prose():
    r = Rec()
    r.echo("do it")
    assert run(r)[0].activity() == "do it"
    r.text("Looking at the tests now.\nMore lines.")
    assert run(r)[0].activity() == "Looking at the tests now."
    r.call("t1", "Bash", {"command": "pytest", "description": "Run tests"})
    assert run(r)[0].activity() == "Bash · Run tests"
    r.output("t1", "ok")
    assert run(r)[0].activity() == "Looking at the tests now."


def test_activity_drops_markdown_marks():
    r = Rec()
    r.text("## Done: **all** tests pass in `make test`")
    assert run(r)[0].activity() == "Done: all tests pass in make test"


def test_an_inbox_message_is_its_own_kind():
    r = Rec()
    r.echo("> from monitor:01M4 · ok · 2026-10-06T10:00:00Z\npytest finished")
    (e,) = run(r)[0].entries()
    assert (e["kind"], e["glyph"], e["title"]) == (
        "inbox",
        "⇄",
        "monitor:01M4 · ok · 2026-10-06T10:00:00Z",
    )


def test_a_call_to_aegis_is_named_by_its_verb():
    r = Rec()
    r.call(
        "t1",
        "mcp__aegis__monitor_start",
        {"description": "Run the tests", "done": "test -f x"},
    )
    r.call(
        "t2",
        "mcp__aegis__queue_enqueue",
        {"queue": "general", "payload": "\nReview PR 12\nmore"},
    )
    a, b = run(r)[0].entries()
    assert (a["title"], a["glyph"], a["summary"]) == (
        "monitor_start",
        "⇄",
        "Run the tests",
    )
    assert b["summary"] == "general: Review PR 12"


def test_an_aegis_reply_is_digested_to_its_ids():
    r = Rec()
    r.call("t1", "mcp__aegis__queue_enqueue", {"queue": "general", "payload": "x"})
    r.output("t1", '{"task_id": "task-1", "status": "running", "position": null}')
    r.call("t2", "mcp__aegis__session_list", {})
    r.output("t2", '[{"handle": "a"}, {"handle": "b"}]')
    a, b = run(r)[0].entries()
    assert (
        a["detail"]["result"] == "task-1 · running"
        and b["detail"]["result"] == "2 items"
    )


FILE_REC = {
    "file_id": "AbCdEfGhIjKlMnOpQrStUv",
    "name": "informe año.png",
    "mime": "image/png",
    "size": 48213,
    "preview": "image",
    "excerpt": None,
    "caption": "Weekly cost",
}


def test_a_sent_file_is_its_own_kind():
    r = Rec()
    r.own("file", **FILE_REC)
    (e,) = run(r)[0].entries()
    url = "/files/AbCdEfGhIjKlMnOpQrStUv/informe%20a%C3%B1o.png"
    assert (e["kind"], e["status"], e["glyph"], e["title"]) == (
        "file",
        "ok",
        "▤",
        "informe año.png",
    )
    assert e["summary"] == "47 KB · image/png"
    assert e["md"] == "Weekly cost"
    assert e["detail"] == {
        "file_id": "AbCdEfGhIjKlMnOpQrStUv",
        "url": url,
        "download": url + "?download=1",
        "preview": "image",
        "mime": "image/png",
        "size": 48213,
        "excerpt": None,
    }


def test_activity_names_the_latest_sent_file():
    r = Rec()
    r.text("Rendering the chart.")
    r.own("file", **FILE_REC)
    assert run(r)[0].activity() == "sent informe año.png"
    r.text("Done.")
    assert run(r)[0].activity() == "Done."
