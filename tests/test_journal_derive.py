import json

from aegis.journal.derive import Deriver
from aegis.transcript.entries import PARSERS, Fold

from .test_fold import Rec


def derive(rec: Rec, handle: str = "") -> list:
    d = Deriver(handle)
    return [row for r in rec.records for row in d.feed(r)]


def spawned(r: Rec, cwd="/w", handle="calm-hopper"):
    r.own("spawn", agent="opus", model="opus", cwd=cwd, handle=handle)


def test_a_turn_that_edits_is_one_row_with_its_files():
    r = Rec()
    spawned(r)
    r.own("send", text="fix the bug")
    r.call(
        "t1", "Edit", {"file_path": "src/a.py", "old_string": "x", "new_string": "y"}
    )
    r.output("t1", "ok")
    r.call("t2", "Write", {"file_path": "/abs/b.md", "content": "z"})
    r.output("t2", "ok")
    r.result()
    rows = derive(r)
    assert [x.kind for x in rows] == ["session", "turn"]
    turn = rows[1]
    assert turn.text == "fix the bug" and turn.handle == "calm-hopper"
    assert turn.touches == [("edit", "/w/src/a.py"), ("write", "/abs/b.md")]
    assert turn.source.startswith(f"e{r.records[-1]['i']}.")


def test_a_turn_end_line_names_the_turn_and_a_turn_without_writes_or_line_is_skipped():
    r = Rec()
    spawned(r)
    r.own("send", text="just look")
    r.result()
    r.own("send", text="do it")
    r.own("turn_end", attention="done", line="did the thing", replies=[])
    r.result()
    rows = derive(r)
    assert [(x.kind, x.text) for x in rows] == [
        ("session", "spawned in /w"),
        ("turn", "did the thing"),
    ]


def test_a_failed_edit_touches_nothing():
    r = Rec()
    spawned(r)
    r.own("send", text="try")
    r.call("t1", "Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"})
    r.output("t1", "not found", is_error=True)
    r.result()
    assert [x.kind for x in derive(r)] == ["session"]


def test_a_commit_in_bash_output_is_a_row_with_its_directory():
    r = Rec()
    spawned(r)
    r.own("send", text="commit")
    r.call("t1", "Bash", {"command": "cd /r/x && git commit -m 'feat: y'"})
    r.output("t1", "[main 1a2b3c4] feat: y\n 1 file changed")
    r.result()
    commit = [x for x in derive(r) if x.kind == "commit"][0]
    assert commit.text == "1a2b3c4 feat: y · main"
    assert commit.commit == ("/r/x", "1a2b3c4") and commit.source == "t1"


def test_plan_items_are_rows_once_when_they_turn_done():
    r = Rec()
    spawned(r)
    r.own(
        "plan",
        items=[{"text": "a", "state": "doing"}, {"text": "b", "state": "pending"}],
    )
    r.own(
        "plan", items=[{"text": "a", "state": "done"}, {"text": "b", "state": "doing"}]
    )
    r.own(
        "plan", items=[{"text": "a", "state": "done"}, {"text": "b", "state": "done"}]
    )
    assert [x.text for x in derive(r) if x.kind == "plan"] == ["a", "b"]


def test_a_note_carries_its_tag_and_paths():
    r = Rec()
    spawned(r)
    r.own("journal_note", text="chose sqlite", tag="decision", paths=["src/j"])
    note = derive(r)[-1]
    assert (note.kind, note.tag, note.text, note.touches) == (
        "note",
        "decision",
        "chose sqlite",
        [("note", "/w/src/j")],
    )


def test_a_rename_is_a_row_and_later_rows_carry_the_new_handle():
    r = Rec()
    spawned(r, handle="old-name")
    r.own("name", handle="new-name", title="")
    r.own("journal_note", text="x", tag="milestone", paths=[])
    rows = derive(r)
    assert rows[1].text == "renamed from old-name to new-name"
    assert rows[2].handle == "new-name"


def test_close_ends_an_open_turn_and_is_a_row():
    r = Rec()
    spawned(r)
    r.own("send", text="write")
    r.call("t1", "Write", {"file_path": "n.md", "content": "z"})
    r.output("t1", "ok")
    r.own("close")
    assert [x.kind for x in derive(r)][-2:] == ["turn", "session"]


def test_a_spawn_record_without_a_handle_keeps_the_given_one():
    """Review focus 1 at the unit level: stores from before the handle was recorded."""
    r = Rec()
    r.own("spawn", agent="opus", model="opus", cwd="/w")
    assert derive(r, handle="from-meta")[0].handle == "from-meta"


def live(rec: Rec, handle: str = "") -> list:
    """The rows as a session makes them: the Fold parses each harness record and
    applies it, and the Deriver gets the Fold's events."""
    fold, d, out = Fold(), Deriver(handle), []
    for r in rec.records:
        if r.get("src") in PARSERS:
            events = fold.parse(r["src"], r["line"])
            fold.apply(r, events)
            out += d.feed(r, events)
        else:
            fold.apply(r)
            out += d.feed(r)
    return out


def test_live_and_rebuild_make_the_same_rows_for_a_claude_turn():
    r = Rec()
    spawned(r)
    r.own("send", text="fix")
    r.call("t1", "Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"})
    r.output("t1", "ok")
    r.call("t2", "Bash", {"command": "git commit -m 'feat: y'"})
    r.output("t2", "[main 1a2b3c4] feat: y\n 1 file changed")
    r.result()
    assert derive(r) == live(r) and len(derive(r)) == 3


def test_live_and_rebuild_make_the_same_rows_for_an_interrupted_codex_turn():
    def line(method, **params):
        return json.dumps({"method": method, "params": params})

    t = "thr"
    fc = {
        "id": "it1",
        "type": "fileChange",
        "status": "inProgress",
        "changes": [{"path": "a.py", "kind": {"type": "update"}, "diff": "-x\n+y"}],
    }
    r = Rec()
    spawned(r)
    r._add(src="codex", line=line("aegis/thread", thread={"id": t}))
    r.own("send", text="first")
    r._add(src="codex", line=line("turn/started", threadId=t))
    r._add(src="codex", line=line("item/started", threadId=t, item=fc))
    r.own("stop")
    r._add(
        src="codex",
        line=line("item/completed", threadId=t, item={**fc, "status": "completed"}),
    )
    r._add(
        src="codex",
        line=line("turn/completed", threadId=t, turn={"status": "interrupted"}),
    )
    r.own("send", text="second, only looks")
    r._add(src="codex", line=line("turn/started", threadId=t))
    r._add(
        src="codex",
        line=line("turn/completed", threadId=t, turn={"status": "completed"}),
    )
    assert derive(r) == live(r)


def test_a_plan_item_that_turns_done_again_is_a_row_again():
    r = Rec()
    spawned(r)
    r.own("plan", items=[{"text": "run tests", "state": "done"}])
    r.own(
        "plan",
        items=[
            {"text": "add B", "state": "doing"},
            {"text": "run tests", "state": "pending"},
        ],
    )
    r.own(
        "plan",
        items=[
            {"text": "add B", "state": "done"},
            {"text": "run tests", "state": "done"},
        ],
    )
    assert [x.text for x in derive(r) if x.kind == "plan"] == [
        "run tests",
        "add B",
        "run tests",
    ]


def init(r: Rec, cwd: str):
    r.claude({"type": "system", "subtype": "init", "session_id": "s", "cwd": cwd})


def commit_rows(rows):
    return [x.commit for x in rows if x.kind == "commit"]


def test_an_init_cwd_is_where_the_session_works():
    """Claude reports the session's real directory (a worktree it entered) on
    init; a commit with no cd is filed there, not under the spawn's."""
    r = Rec()
    spawned(r, cwd="/home")
    init(r, "/r/wt")
    r.own("send", text="commit")
    r.call("t1", "Bash", {"command": "git commit -m x"})
    r.output("t1", "[topic 1a2b3c4] x")
    r.result()
    assert commit_rows(derive(r)) == [("/r/wt", "1a2b3c4")]


def test_a_cd_in_claudes_bash_stays_for_later_calls_but_not_a_subagents():
    r = Rec()
    spawned(r, cwd="/w")
    r.own("send", text="go")
    r.call("t1", "Bash", {"command": "cd /r/x"})
    r.output("t1", "")
    r.call("t2", "Bash", {"command": "cd /elsewhere && ls"}, parent="task")
    r.output("t2", "a")
    r.call("t3", "Bash", {"command": "git commit -m x"})
    r.output("t3", "[main 1a2b3c4] x")
    r.call("t4", "Write", {"file_path": "rel.md", "content": "z"})
    r.output("t4", "ok")
    r.result()
    rows = derive(r)
    assert commit_rows(rows) == [("/r/x", "1a2b3c4")]
    assert ("write", "/r/x/rel.md") in rows[-1].touches


def test_a_commit_whose_bash_call_failed_afterwards_is_a_row():
    """git commit && git push with the push rejected; gh pr merge from a
    worktree exits non-zero after merging."""
    r = Rec()
    spawned(r, cwd="/r/x")
    r.own("send", text="ship")
    r.call("t1", "Bash", {"command": "git commit -m x && git push"})
    r.output("t1", "Exit code 1\n[main 1a2b3c4] x\n ! [rejected] main", is_error=True)
    r.call("t2", "Bash", {"command": "gh pr merge 7 --squash"})
    r.output("t2", "✓ Squashed and merged pull request #7\nfatal: x", is_error=True)
    r.call("t3", "Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"})
    r.output("t3", "not found", is_error=True)
    r.result()
    rows = derive(r)
    assert commit_rows(rows) == [("/r/x", "1a2b3c4")]
    assert [x.text for x in rows if x.kind == "pr"] == ["merged #7"]
    assert [x.kind for x in rows if x.kind == "turn"] == []


def test_a_commit_row_names_the_directories_the_turn_wrote_in():
    r = Rec()
    spawned(r, cwd="/w")
    r.own("send", text="go")
    r.call("t1", "Write", {"file_path": "/r/y/src/a.py", "content": "z"})
    r.output("t1", "ok")
    r.call("t2", "Bash", {"command": "git commit -m x"})
    r.output("t2", "[main 1a2b3c4] x")
    r.result()
    (commit,) = [x for x in derive(r) if x.kind == "commit"]
    assert commit.near == ("/r/y/src",)


def test_two_commits_in_one_call_each_get_their_own_repo():
    r = Rec()
    spawned(r, cwd="/w")
    r.call("t1", "Bash", {"command": "git -C a commit -m x && git -C b commit -m y"})
    r.output("t1", "[main 1111111] x\n[main 2222222] y")
    assert commit_rows(derive(r)) == [("/w/a", "1111111"), ("/w/b", "2222222")]


def test_live_and_rebuild_agree_on_init_cwd_cd_heredoc_and_failed_commits():
    r = Rec()
    spawned(r, cwd="/home")
    init(r, "/r/wt")
    r.own("send", text="go")
    r.call("t1", "Bash", {"command": "cd /r/x"})
    r.output("t1", "")
    r.call(
        "t2",
        "Bash",
        {"command": "cd /r/y && git commit -m \"$(cat <<'EOF'\nfeat: y\nEOF\n)\""},
    )
    r.output("t2", "[main 1a2b3c4] feat: y")
    r.call("t3", "Bash", {"command": "git commit -m z && git push"})
    r.output("t3", "Exit code 1\n[main 5d6e7f8] z", is_error=True)
    r.result()
    rows = derive(r)
    assert commit_rows(rows) == [("/r/y", "1a2b3c4"), ("/r/y", "5d6e7f8")]
    assert rows == live(r)
