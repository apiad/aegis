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
