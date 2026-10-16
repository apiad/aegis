from aegis.workflow_log import RunLog, fold


def test_a_run_folds_from_its_records(tmp_path):
    log = RunLog(tmp_path / "r1.jsonl")
    for rec in (
        {
            "t": 1,
            "ev": "armed",
            "run": "r1",
            "owner": "L1",
            "text": "id: x",
            "args": {},
        },
        {"t": 2, "ev": "approved"},
        {
            "t": 3,
            "ev": "node_started",
            "node": "write",
            "log_id": "L2",
            "say": "Write it.",
        },
        {
            "t": 9,
            "ev": "result",
            "node": "write",
            "value": {"path": "a.md"},
            "summary": "Wrote a.md.",
        },
        {"t": 10, "ev": "node_done", "node": "write"},
        {"t": 11, "ev": "finished", "status": "done"},
    ):
        log.append(rec)
    st = fold(log.read())
    assert st.status == "done" and st.started == 2 and st.ended == 11
    n = st.nodes["write"]
    assert (n.status, n.log_id, n.result, n.summary) == (
        "done",
        "L2",
        {"path": "a.md"},
        "Wrote a.md.",
    )
    assert [s["text"] for s in st.story] == [
        "Write it. Wrote a.md.",
        "The workflow is done.",
    ]


def test_a_damaged_line_is_skipped(tmp_path):
    p = tmp_path / "r1.jsonl"
    p.write_text(
        '{"t": 1, "ev": "armed", "run": "r1", "owner": "L1", "text": "", "args": {}}\nnot json\n'
    )
    assert fold(RunLog(p).read()).status == "armed"


def test_outputs_items_and_a_failed_run_fold(tmp_path):
    log = RunLog(tmp_path / "r2.jsonl")
    for rec in (
        {
            "t": 1,
            "ev": "armed",
            "run": "r2",
            "owner": "L1",
            "text": "id: x",
            "args": {},
        },
        {"t": 2, "ev": "approved"},
        {
            "t": 3,
            "ev": "map_items",
            "node": "images",
            "items": [["base", {"id": "base"}]],
        },
        {
            "t": 4,
            "ev": "node_started",
            "node": "images.base.img",
            "log_id": "L2",
            "say": "Build base.",
        },
        {
            "t": 5,
            "ev": "output",
            "node": "images.base.img",
            "name": "tag",
            "value": "v1",
            "summary": "Tagged v1.",
        },
        {
            "t": 6,
            "ev": "failed",
            "node": "mock",
            "error": "over is a str, not a list",
            "say": "Mocks.",
        },
        {"t": 7, "ev": "finished", "status": "failed"},
    ):
        log.append(rec)
    st = fold(log.read())
    assert st.nodes["images"].items == [["base", {"id": "base"}]]
    img = st.nodes["images.base.img"]
    assert img.outputs == {"tag": "v1"} and img.status == "stopped" and img.ended == 7
    assert [s["text"] for s in st.story] == [
        "Build base. Early output tag: Tagged v1.",
        "Mocks. Failed: over is a str, not a list",
    ]


def test_the_steps_records_fold(tmp_path):
    log = RunLog(tmp_path / "r2.jsonl")
    for rec in (
        {
            "t": 1,
            "ev": "armed",
            "run": "r2",
            "owner": "L1",
            "text": "x",
            "args": {},
            "cwd": "/w",
        },
        {"t": 2, "ev": "approved"},
        {
            "t": 3,
            "ev": "node_started",
            "node": "merge",
            "say": "Merge it.",
            "cmd": "gh pr merge 12",
        },
        {"t": 3.1, "ev": "pid", "node": "merge", "pid": 4242, "start": 99},
        {
            "t": 4,
            "ev": "failed",
            "node": "merge",
            "error": "exit 1: no",
            "retrying": 2,
            "of": 3,
        },
        {"t": 4, "ev": "retry", "node": "merge"},
        {"t": 5, "ev": "node_started", "node": "pause", "say": "Pause.", "until": 65},
        {
            "t": 6,
            "ev": "loop_started",
            "node": "review",
            "max": 3,
            "say": "Fix until approved.",
        },
        {"t": 6, "ev": "round", "node": "review", "n": 1},
        {"t": 7, "ev": "judge_started", "node": "review", "round": "1", "log_id": "J1"},
        {
            "t": 8,
            "ev": "verdict",
            "node": "review",
            "round": "1",
            "pass": False,
            "why": "Tests fail.",
            "summary": "No.",
        },
        {
            "t": 8,
            "ev": "until",
            "node": "review",
            "n": 1,
            "pass": False,
            "why": "Tests fail.",
        },
        {
            "t": 9,
            "ev": "branch",
            "node": "size",
            "took": "none",
            "why": "exit 1",
            "say": "Branch.",
        },
    ):
        log.append(rec)
    st = fold(log.read())
    assert st.cwd == "/w"
    merge = st.nodes["merge"]
    assert (merge.status, merge.attempts, merge.pid, merge.cmd) == (
        "pending",
        1,
        None,
        "",
    )
    assert st.nodes["pause"].until == 65
    review = st.nodes["review"]
    assert (review.max, review.round, review.untils[1]["pass"]) == (3, 1, False)
    assert review.judges["1"] == {"log_id": "J1", "pass": False, "why": "Tests fail."}
    assert st.nodes["size"].took == "none" and st.nodes["size"].status == "done"
    assert any("Retrying, attempt 2 of 3." in s["text"] for s in st.story)


def _run(*recs):
    head = {
        "t": 1,
        "ev": "armed",
        "run": "r",
        "owner": "L1",
        "text": "id: x",
        "args": {},
    }
    return fold([head, {"t": 2, "ev": "approved"}, *recs])


def test_a_final_failure_opens_an_item_and_a_retrying_one_does_not():
    st = _run(
        {"t": 3, "ev": "node_started", "node": "a", "log_id": "L2", "say": "Do a."},
        {"t": 4, "ev": "failed", "node": "a", "error": "boom", "retrying": 2, "of": 2},
    )
    assert st.open == {} and st.nodes["a"].status == "pending"
    st = _run(
        {"t": 3, "ev": "node_started", "node": "a", "log_id": "L2", "say": "Do a."},
        {"t": 4, "ev": "failed", "node": "a", "error": "boom"},
    )
    assert st.status == "running"
    assert st.open["a"] == {
        "kind": "failed",
        "node": "a",
        "error": "boom",
        "told": False,
    }
    st2 = _run(
        {
            "t": 3,
            "ev": "failed",
            "node": "a",
            "error": "stopped by the main agent",
            "by": "main",
        },
    )
    assert st2.open["a"]["told"] is True


def test_woke_marks_told_and_retried_resets_every_listed_instance():
    st = _run(
        {"t": 3, "ev": "map_items", "node": "m", "items": [["x", 1]]},
        {"t": 4, "ev": "node_started", "node": "m.x.a", "log_id": "L2", "say": "A."},
        {"t": 5, "ev": "failed", "node": "m.x.a", "error": "boom"},
        {"t": 6, "ev": "woke", "to": "m.x.a"},
    )
    assert st.open["m.x.a"]["told"] is True
    st = _run(
        {"t": 3, "ev": "map_items", "node": "m", "items": [["x", 1]]},
        {"t": 4, "ev": "failed", "node": "m", "error": "over has no value"},
        {
            "t": 5,
            "ev": "retried",
            "node": "m",
            "reset": ["m"],
            "text": "Fixed the list.",
        },
    )
    assert st.open == {} and st.nodes["m"].items is None and st.nodes["m"].attempts == 1
    assert st.story[-1]["text"].startswith("Retrying")


def test_skipped_is_a_result_the_engine_reads_as_done():
    st = _run(
        {"t": 3, "ev": "node_started", "node": "a", "log_id": "L2", "say": "Do a."},
        {"t": 4, "ev": "failed", "node": "a", "error": "boom"},
        {
            "t": 5,
            "ev": "skipped",
            "node": "a",
            "value": {"pr": 9},
            "summary": "PR 9 was merged by hand.",
        },
    )
    n = st.nodes["a"]
    assert (n.status, n.result, st.open) == ("skipped", {"pr": 9}, {})


def test_gates_questions_and_notes_fold_into_open_items_and_the_story():
    st = _run(
        {
            "t": 3,
            "ev": "gate_opened",
            "node": "g",
            "by": "person",
            "ask": "Go?",
            "shown": {"a": 1},
            "say": "Ask.",
        },
        {"t": 4, "ev": "asked", "node": "w", "q": "q1", "question": "Which branch?"},
        {"t": 5, "ev": "note", "text": "Answered main because the plan says so."},
    )
    assert st.nodes["g"].gate == {"by": "person", "ask": "Go?", "shown": {"a": 1}}
    assert st.open["g"]["told"] is True and st.open["q1"]["kind"] == "ask"
    assert [s["mark"] for s in st.story] == ["ask", "ask", "note"]
    st = _run(
        {
            "t": 3,
            "ev": "gate_opened",
            "node": "g",
            "by": "main",
            "ask": "Go?",
            "shown": {},
            "say": "Ask.",
        },
        {
            "t": 4,
            "ev": "gate_answered",
            "node": "g",
            "by": "main",
            "go": True,
            "value": 3,
            "text": "Go.",
        },
        {"t": 5, "ev": "asked", "node": "w", "q": "q1", "question": "?"},
        {"t": 6, "ev": "answered", "q": "q1", "text": "main"},
    )
    assert st.nodes["g"].status == "done" and st.nodes["g"].result == {
        "value": 3,
        "by": "main",
    }
    assert st.open == {}


def test_edits_replace_apply_and_reject():
    st = _run(
        {
            "t": 3,
            "ev": "edit_proposed",
            "v": 2,
            "text": "two",
            "diff": [],
            "touched": [],
            "commands": {},
        },
        {
            "t": 4,
            "ev": "edit_proposed",
            "v": 3,
            "text": "three",
            "diff": [],
            "touched": [],
            "commands": {},
        },
        {"t": 5, "ev": "edit_applied", "v": 3},
    )
    assert st.text == "three" and st.version == 3 and st.proposal is None
    assert [e["status"] for e in st.edits] == ["replaced", "applied"]
    assert st.open["v3"] == {"kind": "edit", "outcome": "applied", "told": False}
    st = _run({"t": 3, "ev": "woke", "to": "nothing"})  # an unknown item is ignored
    assert st.open == {}


def test_a_stop_stops_leaves_and_keeps_a_loops_round():
    st = _run(
        {
            "t": 3,
            "ev": "loop_started",
            "node": "l",
            "max": 3,
            "round": 2,
            "say": "Loop.",
        },
        {"t": 4, "ev": "node_started", "node": "l.2.a", "log_id": "L2", "say": "A."},
        {"t": 5, "ev": "stopped"},
    )
    assert st.nodes["l"].status == "running" and st.nodes["l"].round == 2
    assert st.nodes["l.2.a"].status == "stopped"


def test_rearmed_keeps_done_nodes_and_resets_the_rest():
    st = _run(
        {"t": 3, "ev": "node_started", "node": "a", "say": "A."},
        {"t": 4, "ev": "node_done", "node": "a"},
        {"t": 5, "ev": "node_started", "node": "b", "say": "B."},
        {"t": 6, "ev": "stopped"},
        {"t": 7, "ev": "rearmed", "v": 1, "text": "id: x", "reset": ["b"]},
    )
    assert st.status == "armed" and st.ended is None
    assert st.nodes["a"].status == "done" and st.nodes["b"].status == "pending"
    assert st.nodes["b"].attempts == 1


def test_a_replaced_run_names_its_successor():
    st = fold(
        [
            {"t": 1, "ev": "armed", "run": "r", "owner": "L1", "text": "x", "args": {}},
            {"t": 2, "ev": "replaced", "by": "r2"},
        ]
    )
    assert (st.status, st.replaced_by) == ("replaced", "r2")


def test_a_probe_start_marks_an_if_running_and_starts_its_clock():
    st = fold(
        [
            {
                "t": 1,
                "ev": "armed",
                "run": "r1",
                "owner": "L1",
                "text": "id: x",
                "args": {},
            },
            {"t": 2, "ev": "approved"},
            {"t": 5, "ev": "probe_started", "node": "ask", "say": "Ask."},
        ]
    )
    n = st.nodes["ask"]
    assert (n.status, n.started, n.took) == ("running", 5, None)
    assert not any("Ask." in s["text"] for s in st.story)
