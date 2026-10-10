import json
from pathlib import Path

import pytest

from aegis.transcript.entries import EMPTY_STANDING, Fold, fold_records
from aegis.transcript.store import read_store


class Rec:
    """Builds store records the way a session writes them."""

    def __init__(self):
        self.records = []
        self.skew = 0.0

    def _add(self, **kw):
        ts = 1000.0 + len(self.records) + self.skew
        r = {"i": len(self.records), "ts": ts, **kw}
        self.records.append(r)
        return r

    def wait(self, s: float) -> None:
        """The next record comes ``s`` seconds later than it otherwise would."""
        self.skew += s

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


def test_a_bash_row_is_named_by_its_opening_comment():
    r = Rec()
    r.call("t1", "Bash", {"command": "# Count open issues\ngh issue list | wc -l"})
    r.call(
        "t2",
        "Bash",
        {"command": "#Run tests\npytest -q", "description": "Run the suite"},
    )
    r.call("t3", "Bash", {"command": "pytest -q", "description": "Run tests"})
    r.call("t4", "Bash", {"command": "ls -la /tmp"})
    a, b, c, d = run(r)[0].entries()
    assert [e["summary"] for e in (a, b, c, d)] == [
        "Count open issues",
        "Run tests",
        "Run tests",
        "ls -la /tmp",
    ]
    assert a["detail"]["args"] == "# Count open issues\ngh issue list | wc -l"
    assert b["detail"]["args"] == "#Run tests\npytest -q"
    assert c["detail"]["args"] == "# Run tests\npytest -q"


def test_a_bash_verdict_skips_the_lines_claude_code_appends():
    r = Rec()
    r.call("t1", "Bash", {"command": "cd /x && pytest -q"})
    r.output("t1", "....\n3 passed in 0.2s\nShell cwd was reset to /home/a/w")
    r.call("t2", "Bash", {"command": "sed -i s/a/b/ x.py && echo done"})
    r.output(
        "t2",
        "2 files changed\n[This command modified 1 file you've previously read:"
        " x.py. Call Read before editing.]",
    )
    a, b = run(r)[0].entries()
    assert a["detail"]["result"] == "3 passed in 0.2s"
    assert b["detail"]["result"] == "2 files changed"


def test_a_failed_bash_verdict_skips_them_too():
    r = Rec()
    r.call("t1", "Bash", {"command": "cd /x && make test"})
    r.output(
        "t1",
        "Exit code 2\n1 failed, 3 passed\nShell cwd was reset to /home/a/w",
        is_error=True,
    )
    (e,) = run(r)[0].entries()
    assert e["detail"]["result"] == "Exit code 2 · 1 failed, 3 passed"


def test_a_failure_starts_closed_like_every_tool_row():
    r = Rec()
    r.call("t1", "Bash", {"command": "mmdc"})
    r.output("t1", "Exit code 1\nError: Parse error on line 9", is_error=True)
    f, _ = run(r)
    (e,) = f.entries()
    assert e["status"] == "err" and "collapsed" not in e["detail"]
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
    r.own("resume", resume_id="cs")
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == [
        "the server stopped during a turn",
        "resumed",
    ]


def test_activity_prefers_a_running_call_then_the_latest_prose():
    r = Rec()
    r.echo("do it")
    assert run(r)[0].activity() == "waiting for the model"
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
    # A record from before file_send took a list: a set of one.
    assert e["detail"] == {
        "files": [
            {
                "name": "informe año.png",
                "summary": "47 KB · image/png",
                "file_id": "AbCdEfGhIjKlMnOpQrStUv",
                "url": url,
                "download": url + "?download=1",
                "preview": "image",
                "mime": "image/png",
                "size": 48213,
                "excerpt": None,
            }
        ]
    }


def test_activity_names_the_latest_sent_file():
    r = Rec()
    r.text("Rendering the chart.")
    r.own("file", **FILE_REC)
    assert run(r)[0].activity() == "sent informe año.png"
    r.text("Done.")
    assert run(r)[0].activity() == "Done."


FIXTURE = Path("tests/fixtures/slash-commands.jsonl")


def local(command, args, text):
    return {
        "type": "assistant",
        "local_command_run": {"command": command, "args": args},
        "message": {
            "model": "<synthetic>",
            "content": [{"type": "text", "text": text}],
        },
    }


def test_the_recorded_slash_commands_fold_with_nothing_left_pending():
    records, damaged = read_store(FIXTURE)
    assert damaged == 0
    es = fold_records(records).entries()
    assert not [e for e in es if e["status"] == "pending"]
    cmds = [(e["title"], e["md"]) for e in es if e["kind"] == "command"]
    assert cmds[0][0] == "/effort high"
    assert cmds[0][1].startswith("Set effort level to high")
    assert ("/compact", "Compacted") in cmds
    users = [e["md"] for e in es if e["kind"] == "user"]
    assert users == ["/hello world", "what did I say before? one line"]
    raw = [
        e
        for e in es
        if "<command-" in (e["md"] or "") or "<local-command" in (e["md"] or "")
    ]
    assert not raw
    assert any(e["summary"].startswith("context cleared") for e in es)


def test_a_command_waiting_for_the_turn_end_does_not_take_a_prompts_echo():
    r = Rec()
    r.own("send", text="/sleep 2")
    r.call("t1", "Bash", {"command": "sleep 2"})
    r.own("send", text="/effort low")
    r.own("send", text="steer")
    r.echo("steer")  # injected at the tool boundary
    r.output("t1", "slept")
    r.result()
    r.claude(local("effort", "low", "Set effort level to low"))
    r.claude(
        {"type": "result", "subtype": "success", "num_turns": 0, "total_cost_usd": 0.01}
    )
    f, _ = run(r)
    es = f.entries()
    read = [e["md"] for e in es if e["kind"] == "user" and e["status"] == "ok"]
    assert read == ["steer"]
    assert [e["title"] for e in es if e["kind"] == "command"] == ["/effort low"]
    # Nothing echoed /sleep here; the fake echoes it, real Claude runs a script.
    assert [e["md"] for e in es if e["status"] == "pending"] == ["/sleep 2"]


def test_the_escape_leading_space_matches_the_stripped_echo():
    r = Rec()
    r.own("send", text=" /compact now")
    r.echo("/compact now")
    f, ops = run(r)
    assert ops[1][0] == {"remove": "pending:0"}
    assert [e["md"] for e in f.entries()] == ["/compact now"]


def test_output_with_no_command_waiting_makes_no_entry():
    r = Rec()
    r.claude(
        {
            "type": "user",
            "isReplay": True,
            "message": {
                "content": "<local-command-stdout>Set model to Sonnet 5</local-command-stdout>"
            },
        }
    )
    f, _ = run(r)
    assert f.entries() == []


def test_a_local_only_turn_leaves_no_done_row_but_compact_shows_its_cost():
    r = Rec()
    r.claude(
        {
            "type": "result",
            "subtype": "success",
            "num_turns": 0,
            "total_cost_usd": 0.0,
            "duration_ms": 0,
        }
    )
    r.claude(
        {
            "type": "result",
            "subtype": "success",
            "num_turns": 0,
            "total_cost_usd": 0.04,
            "duration_ms": 3000,
        }
    )
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == ["done in 3.0s · $0.04"]


def test_configure_records_read_as_one_line():
    r = Rec()
    r.own("configure", model="sonnet", effort="max", when="next_turn")
    r.own("configure", permission="read", when="on_resume")
    r.own("configure", effort="low")
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == [
        "model → sonnet · effort → max (from the next turn)",
        "permission → read (when it resumes)",
        "effort → low",
    ]


def test_a_set_model_note_does_not_take_a_queued_commands_place():
    r = Rec()
    r.own("send", text="/compact")
    r.claude(
        {
            "type": "user",
            "isReplay": True,
            "message": {
                "content": "<local-command-stdout>Set model to Opus</local-command-stdout>"
            },
        }
    )
    r.claude(
        {
            "type": "user",
            "isReplay": True,
            "message": {
                "content": "<local-command-stdout>Compacted </local-command-stdout>"
            },
        }
    )
    f, _ = run(r)
    assert [(e["kind"], e["title"], e["md"]) for e in f.entries()] == [
        ("command", "/compact", "Compacted")
    ]


def test_init_and_exit_name_their_harness():
    r = Rec()
    r.claude(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "s",
            "model": "m",
            "claude_code_version": "2.1",
        }
    )
    r.own("exit", code=1, stderr_tail=[], harness="OpenCode")
    r.own("exit", code=2, stderr_tail=[])  # a record from before the label
    summaries = [e["summary"] for e in run(r)[0].entries()]
    assert summaries[0] == "Claude Code 2.1 · m"
    assert "OpenCode exited with code 1" in summaries
    assert "claude exited with code 2" in summaries


def test_a_live_entry_whose_part_never_closed_is_dropped_at_the_turns_end():
    from aegis.claude.stream import Delta, Result

    f = Fold()
    f.live([Delta(key="prt_1", kind="prose", text="half an ans")])
    assert [e["md"] for e in f.entries()] == ["half an ans"]
    ops = f._event("e9.0", 9.0, Result(False, "success", 10, None, None, None))
    assert {"remove": "prt_1"} in ops
    assert all(e["id"] != "prt_1" for e in f.entries())


def test_a_harness_error_loses_the_send_it_answers_and_a_reset_is_said():
    r = Rec()
    r.own("send", text="/nope x")
    r.own("harness_error", text="/nope failed: 400", line="/nope x")
    r.own(
        "reset", text="OpenCode no longer had this conversation; it started a new one"
    )
    e = run(r)[0].entries()
    assert [(x["kind"], x["status"]) for x in e] == [
        ("user", "lost"), ("error", "err"), ("system", "ok"),
    ]  # fmt: skip
    assert e[1]["summary"] == "/nope failed: 400"


def test_activity_waits_for_the_model_until_the_turn_says_something():
    r = Rec()
    r.echo("do it")
    assert run(r)[0].activity() == "waiting for the model"
    r.text("On it.")
    assert run(r)[0].activity() == "On it."


def plan(*pairs):
    return [{"text": t, "state": s} for t, s in pairs]


def test_standing_starts_empty_and_is_the_same_object_until_it_changes():
    rec = Rec()
    rec.own("send", text="hi")
    rec.echo("hi")
    f, _ = run(rec)
    assert f.standing == EMPTY_STANDING


def test_last_message_is_the_latest_agent_message_and_survives_a_refold():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.text("first")
    rec.call("task1", "Task", {"description": "look", "prompt": "look"})
    rec.text("second")
    rec.text("a subagent's text is not the turn's message", parent="task1")
    rec.result()
    f, _ = run(rec)
    prose = [e["id"] for e in f.entries() if e["kind"] == "prose"]
    assert len(prose) == 2
    assert f.standing["last_message"] == prose[-1]
    again, _ = run(rec)
    assert again.standing == f.standing


def test_a_plan_record_sets_the_plan_and_did_tracks_the_last_item_finished():
    rec = Rec()
    rec.own("plan", items=plan(("read", "doing"), ("fix", "pending")))
    f, _ = run(rec)
    assert [(i["text"], i["state"]) for i in f.standing["plan"]] == [
        ("read", "doing"),
        ("fix", "pending"),
    ]
    assert f.standing["did"] == ""
    before = f.standing
    f.apply(rec.own("plan", items=plan(("read", "done"), ("fix", "doing"))))
    assert f.standing is not before
    assert f.standing["did"] == "read"
    same = f.standing
    f.apply(rec.own("plan", items=plan(("read", "done"), ("fix", "doing"))))
    assert f.standing is same  # nothing changed, same object


def test_a_turn_end_lasts_until_the_next_send_or_a_later_turn_ends_without_one():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.own(
        "turn_end", attention="needs_you", line="Rebase or merge?", replies=["rebase"]
    )
    rec.result()
    f, _ = run(rec)
    assert f.standing["report"] == {
        "attention": "needs_you",
        "line": "Rebase or merge?",
        "replies": ["rebase"],
    }
    # A turn Claude starts on its own and ends without turn_end drops it.
    f.apply(rec.text("the background task finished"))
    f.apply(rec.result())
    assert f.standing["report"] is None


def test_a_send_clears_the_report_at_once():
    rec = Rec()
    rec.own("turn_end", attention="review", line="Read the spec", replies=[])
    rec.result()
    f, _ = run(rec)
    assert f.standing["report"]["attention"] == "review"
    f.apply(rec.own("send", text="ok"))
    assert f.standing["report"] is None


def test_the_last_turn_end_of_a_turn_wins():
    rec = Rec()
    rec.own("turn_end", attention="done", line="first", replies=[])
    rec.own("turn_end", attention="needs_you", line="second", replies=[])
    rec.result()
    f, _ = run(rec)
    assert f.standing["report"]["line"] == "second"


def test_failures_set_turn_error_and_a_persons_interrupt_does_not():
    rec = Rec()
    rec.own("send", text="go")
    rec.result(is_error=True, subtype="error_during_execution")
    f, _ = run(rec)
    assert f.standing["turn_error"] == "turn failed (error_during_execution)"
    f.apply(rec.own("send", text="again"))
    assert f.standing["turn_error"] == ""
    f.apply(rec.own("interrupt"))
    f.apply(rec.result(is_error=True, subtype="error_during_execution"))
    assert f.standing["turn_error"] == ""
    f.apply(rec.own("exit", code=3, stderr_tail=[]))
    assert f.standing["turn_error"] == "claude exited with code 3"
    f.apply(rec.own("send", text="resume"))
    f.apply(rec.own("interrupt_timeout", after_s=10))
    assert f.standing["turn_error"] == "the interrupt went unanswered for 10s"
    f.apply(rec.result())
    assert f.standing["turn_error"] == ""


def test_a_turn_cut_by_a_server_restart_is_an_error_and_a_persons_stop_is_not():
    rec = Rec()
    rec.own("send", text="go")
    rec.own("server_stopped")
    f, _ = run(rec)
    assert f.standing["turn_error"] == "the server stopped during a turn"
    rec = Rec()
    rec.own("send", text="go")
    rec.own("stop")
    f, _ = run(rec)
    assert f.standing["turn_error"] == ""


def test_a_recap_record_is_an_entry_and_a_send_folds_it():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.text("done it")
    rec.result()
    rec.own(
        "recap",
        upto=3,
        context="fixing it",
        ask="merge?",
        model="m",
        cost_usd=0.004,
        duration_ms=1800,
    )
    f, _ = run(rec)
    (r,) = [e for e in f.entries() if e["kind"] == "recap"]
    assert (
        r["id"] == "e4"
        and r["detail"]["context"] == "fixing it"
        and r["detail"]["folded"] is False
    )
    assert f.last_index == 4
    ops = f.apply(rec.own("send", text="next"))
    assert any(
        op.get("upsert", {}).get("id") == "e4" and op["upsert"]["detail"]["folded"]
        for op in ops
    )


def test_every_recap_before_a_send_folds_and_stays_folded_on_a_refold():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.text("done it")
    rec.result()
    rec.own("recap", upto=3, context="first", ask="")
    rec.own("recap", upto=3, context="refreshed", ask="")
    rec.own("send", text="next")
    f, _ = run(rec)
    recaps = [e for e in f.entries() if e["kind"] == "recap"]
    assert [r["id"] for r in recaps] == ["e4", "e5"]
    assert all(r["detail"]["folded"] for r in recaps)
    g, _ = run(rec)
    assert g.entries() == f.entries()


def test_a_new_recap_folds_the_earlier_ones_without_a_send():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.text("done it")
    rec.result()
    rec.own("recap", upto=3, context="first", ask="")
    rec.own("recap", upto=3, context="refreshed", ask="")
    f, _ = run(rec)
    recaps = {e["id"]: e for e in f.entries() if e["kind"] == "recap"}
    assert recaps["e4"]["detail"]["folded"] is True
    assert recaps["e5"]["detail"]["folded"] is False
    g, _ = run(rec)
    assert g.entries() == f.entries()


def test_a_stop_and_an_exit_after_the_prose_leave_the_content_index():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.text("done it")
    rec.result()
    f, _ = run(rec)
    assert f.content_index == 2
    f.apply(rec.own("stop"))
    f.apply(rec.own("exit", code=1, harness="claude"))
    assert f.content_index == 2 and f.last_index == 5


def test_an_artifact_folds_to_one_entry_that_its_later_records_update():
    r = Rec()
    r.own(
        "artifact",
        artifact_id="art-aaaa0001",
        file_id="F1",
        name="index.html",
        title="Pick",
        caption="Pick one",
        state={"n": 0},
        started=True,
    )
    r.own("artifact_state", artifact_id="art-aaaa0001", state={"n": 1}, by="page")
    r.own("artifact_event", artifact_id="art-aaaa0001", name="hover", data={"n": 1})
    r.own(
        "artifact_submit",
        artifact_id="art-aaaa0001",
        data={"pick": "b"},
        label="Picked B",
    )
    f, ops = run(r)
    (e,) = f.entries()
    assert e["id"] == "art-aaaa0001" and e["kind"] == "artifact"
    assert e["status"] == "submitted" and e["summary"] == "submitted"
    assert e["title"] == "Pick" and e["md"] == "Pick one" and e["glyph"] == "▣"
    d = e["detail"]
    assert d["url"] == "/files/F1/index.html" and d["started"] is True
    assert d["state"] == {"n": 1} and d["state_by"] == "page"
    assert d["state_rev"] == 1  # the artifact_state record, not the event after it
    assert d["events"] == [{"name": "hover", "data": {"n": 1}, "ts": 1002.0}]
    assert d["submitted"] == {"pick": "b"} and d["label"] == "Picked B"
    assert d["ended_ts"] == 1003.0 and e["rev"] == 3
    assert all(op["upsert"]["id"] == "art-aaaa0001" for step in ops for op in step)


def test_a_resend_swaps_the_file_and_keeps_the_rest_and_a_close_collapses():
    r = Rec()
    r.own(
        "artifact",
        artifact_id="art-aaaa0002",
        file_id="F1",
        name="index.html",
        title="T",
        caption=None,
        state=None,
        started=None,
    )
    r.own("artifact_state", artifact_id="art-aaaa0002", state={"k": 1}, by="agent")
    r.own(
        "artifact",
        artifact_id="art-aaaa0002",
        file_id="F2",
        name="index.html",
        title="T",
        caption="now",
        state=None,
        started=True,
    )
    r.own("artifact_close", artifact_id="art-aaaa0002", label=None)
    f, _ = run(r)
    (e,) = f.entries()
    assert e["detail"]["url"] == "/files/F2/index.html" and e["md"] == "now"
    assert e["detail"]["state"] == {"k": 1} and e["detail"]["state_by"] == "agent"
    assert e["detail"]["state_rev"] == 1  # the resend did not set the state
    assert e["status"] == "closed" and e["detail"]["label"] is None
    assert f.activity() == "showed T"


def test_events_keep_the_last_twenty_and_a_record_for_an_unknown_artifact_is_ignored():
    r = Rec()
    r.own("artifact_state", artifact_id="art-nope0000", state={}, by="page")
    r.own(
        "artifact",
        artifact_id="art-aaaa0003",
        file_id="F",
        name="index.html",
        title="T",
        caption=None,
        state=None,
        started=None,
    )
    for n in range(25):
        r.own("artifact_event", artifact_id="art-aaaa0003", name="tick", data=n)
    f, _ = run(r)
    (e,) = f.entries()
    assert e["detail"]["state_rev"] == 1  # the artifact record that created it
    assert [ev["data"] for ev in e["detail"]["events"]] == list(range(5, 25))


def _sent(name, **kw):
    return {
        "file_id": f"id-{name}",
        "name": name,
        "mime": "image/png",
        "size": 2048,
        "preview": "image",
        "excerpt": None,
        **kw,
    }


def test_a_file_set_folds_into_one_entry():
    r = Rec()
    r.own("file", caption=None, files=[_sent("a.png"), _sent("b.png", size=10)])
    f = fold_records(r.records)
    (e,) = f.entries()
    assert f.activity() == "sent a.png +1"
    assert e["title"] == "a.png +1" and e["summary"] == "2 files"
    assert [f["name"] for f in e["detail"]["files"]] == ["a.png", "b.png"]
    assert e["detail"]["files"][1]["summary"] == "10 B · image/png"


def test_a_file_tools_row_carries_the_path_it_used():
    r = Rec()
    r.call("t1", "Read", {"file_path": "/x/a.py"})
    r.output("t1", "print(1)")
    r.call("t2", "NotebookEdit", {"notebook_path": "/x/n.ipynb", "new_source": ""})
    r.call("t3", "Bash", {"command": "ls"})
    f = fold_records(r.records)
    assert f.entry("t1")["detail"]["path"] == "/x/a.py"
    assert f.entry("t2")["detail"]["path"] == "/x/n.ipynb"
    assert "path" not in f.entry("t3")["detail"]


def test_a_peek_opens_its_file_inside_the_row_it_was_asked_from():
    r = Rec()
    r.call("t1", "Read", {"file_path": "/x/a.png"})
    r.output("t1", "png")
    before = fold_records(r.records).entries()
    r.own("peek", entry="t1", files=[_sent("a.png")])
    r.own("peek", entry="nope", files=[_sent("b.png")])
    f = fold_records(r.records)
    assert [e["id"] for e in f.entries()] == [e["id"] for e in before]
    peek = f.entry("t1")["detail"]["peek"]
    assert peek["ts"] == r.records[-2]["ts"]
    assert [x["name"] for x in peek["files"]] == ["a.png"]
    assert f.entry("t1")["detail"]["result"] == "1 line"


def times(f):
    c = f.standing["clock"]
    work = [i["work_s"] for i in f.standing["plan"]]
    return work, (c["work_s"], c["idle_s"], c["at"], c["running"])


def test_no_plan_keeps_no_clock():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.result()
    f, _ = run(r)
    assert f.standing["clock"] is None


def test_a_wait_on_a_monitor_is_work_for_the_doing_item():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"), ("fix", "pending")))  # 1002
    r.wait(100)
    r.result()  # 1103: no turn_end, so the gap is work
    r.wait(50)
    r.own("send", text="> from monitor:m1 · done")  # 1154
    r.echo("> from monitor:m1 · done")  # 1155
    r.own("plan", items=plan(("read", "done"), ("fix", "doing")))  # 1156
    f, _ = run(r)
    assert times(f) == ([154.0, 0.0], (154.0, 0.0, 1156.0, "work"))


@pytest.mark.parametrize("close", ["turn_end", "failed", "interrupted"])
def test_a_turn_that_hands_back_to_the_person_starts_idle(close):
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing")))  # 1002
    if close == "turn_end":
        r.own("turn_end", attention="needs_you", line="ok?", replies=[])
    elif close == "interrupted":
        r.own("interrupt")
    else:
        r.text("trying")
    if close == "failed":
        r.result(is_error=True, subtype="error_during_execution")  # 1004
    else:
        r.result()  # 1004
    r.wait(600)
    r.own("send", text="yes")  # 1605
    f, _ = run(r)
    assert times(f) == ([2.0], (2.0, 601.0, 1605.0, "work"))


def test_work_with_nothing_doing_counts_for_the_plan_only():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"), ("fix", "pending")))  # 1002
    r.own("plan", items=plan(("read", "done"), ("fix", "pending")))  # 1003
    r.wait(10)
    r.own("plan", items=plan(("read", "done"), ("fix", "doing")))  # 1014
    f, _ = run(r)
    assert times(f) == ([1.0, 0.0], (12.0, 0.0, 1014.0, "work"))


def test_a_plan_with_no_text_in_common_starts_from_zero():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing")))  # 1002
    r.wait(30)
    r.own("plan", items=plan(("other", "doing")))  # 1033
    f, _ = run(r)
    assert times(f) == ([0.0], (0.0, 0.0, 1033.0, "work"))


def test_a_turn_with_no_result_works_until_its_last_record():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing")))  # 1002
    r.text("reading")  # 1003
    r.wait(500)
    r.own("server_stopped")  # 1504
    r.own("send", text="again")  # 1505
    f, _ = run(r)
    assert times(f) == ([1.0], (1.0, 502.0, 1505.0, "work"))


def test_the_clock_survives_a_refold():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.own("plan", items=plan(("read", "doing"), ("fix", "pending")))
    r.own("turn_end", attention="done", line="read it", replies=[])
    r.result()
    r.wait(40)
    r.own("send", text="next")
    f, _ = run(r)
    again, _ = run(r)
    assert again.standing == f.standing


def test_a_slash_command_while_the_session_waits_on_the_person_stays_idle():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing")))  # 1002
    r.own("turn_end", attention="needs_you", line="ok?", replies=[])  # 1003
    r.result()  # 1004
    r.wait(100)
    r.own("send", text="/context")  # 1105
    r.result()  # 1106: a local command, no turn_end
    f, _ = run(r)
    assert times(f)[1][3] == "idle"


@pytest.mark.parametrize("kind", ["stop", "exit", "close"])
def test_the_process_ending_during_a_work_gap_starts_idle(kind):
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing")))  # 1002
    r.result()  # 1003: no turn_end, so the gap is work
    r.wait(10)
    r.own(kind, code=0)  # 1014
    f, _ = run(r)
    assert times(f) == ([12.0], (12.0, 0.0, 1014.0, "idle"))


def test_the_work_between_messages_folds_and_what_was_said_does_not():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.claude(
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "hm"}]},
        }
    )
    r.call("t1", "Bash", {"command": "ls"})
    r.output("t1", "a")
    r.text("done")
    r.result()
    f, _ = run(r)
    assert [(e["kind"], e["fold"]) for e in f.entries()] == [
        ("user", 0),
        ("thinking", 1),
        ("tool", 1),
        ("prose", 0),
        ("system", 1),
    ]


def _attached(name):
    return _sent(name, path=f"/s/inbox/log/20261010-101502-{name}")


FULL = (
    "look\n\nAttached files:\n- /s/inbox/log/20261010-101502-a.png (image/png, 2.0 KB)"
)


def test_an_attached_send_shows_what_was_typed_and_one_card():
    r = Rec()
    r.own("send", text=FULL, typed="look", files=[_attached("a.png")])
    r.echo(FULL)
    f, ops = run(r)
    pending = ops[0][-1]["upsert"]
    assert (pending["md"], pending["status"]) == ("look", "pending")
    assert [x["name"] for x in pending["detail"]["files"]] == ["a.png"]
    assert ops[1][0] == {"remove": "pending:0"}
    (user,) = f.entries()
    assert (user["md"], user["status"]) == ("look", "ok")
    assert [x["name"] for x in user["detail"]["files"]] == ["a.png"]
    assert user["detail"]["files"][0]["url"] == "/files/id-a.png/a.png"


def test_a_send_of_files_alone_has_no_text():
    r = Rec()
    full = "Attached files:\n- /s/inbox/log/20261010-101502-a.png (image/png, 2.0 KB)"
    r.own("send", text=full, typed="", files=[_attached("a.png")])
    r.echo(full)
    (user,) = fold_records(r.records).entries()
    assert user["md"] == "" and len(user["detail"]["files"]) == 1


def test_an_attached_echo_takes_its_own_pending_send_not_the_oldest():
    r = Rec()
    r.own("send", text="look")
    r.own("send", text=FULL, typed="look", files=[_attached("a.png")])
    r.echo(FULL)
    r.echo("look")
    f, _ = run(r)
    users = [e for e in f.entries() if e["kind"] == "user"]
    assert [(u["md"], len(u["detail"].get("files", []))) for u in users] == [
        ("look", 1),
        ("look", 0),
    ]
    assert fold_records(r.records).entries() == f.entries()
