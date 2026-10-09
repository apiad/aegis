"""What a transcript looks like on the wire: revisions, deltas and the lazy
detail (DESIGN.md, "The patches add up to the entries")."""

import json
from pathlib import Path

import pytest

from aegis.opencode.stream import DELTA
from aegis.transcript.entries import Fold, fold_records
from aegis.transcript.store import read_store
from aegis.transcript.wire import LAZY, wire, wire_ops

from .test_fold import Rec, run
from .test_opencode_stream import first_prompt, lines

FIX = Path(__file__).parent / "fixtures"


def rebuild(held: list[dict], snap: dict) -> list[dict]:
    """What a client holding ``held`` has after applying ``snap``: removals
    first, then upserts. A dict, like the client's Map, keeps a known id in
    place and appends a new one."""
    shown = {e["id"]: e for e in held}
    for id in snap.get("removed", []):
        shown.pop(id, None)
    for e in snap["entries"]:
        shown[e["id"]] = e
    return list(shown.values())


def test_every_entry_carries_the_index_of_the_record_that_last_changed_it():
    r = Rec()
    r.own("send", text="hello")  # 0: a pending user entry
    r.echo("hello")  # 1: the echo replaces it
    r.call("t1", "Bash", {"command": "ls"})  # 2
    r.output("t1", "a\nb")  # 3: the call's entry changes again
    f, _ = run(r)
    assert {e["id"]: e["rev"] for e in f.entries()} == {"e1.0": 1, "t1": 3}


@pytest.mark.parametrize("name", ["session.jsonl", "slash-commands.jsonl"])
def test_a_delta_from_any_cut_rebuilds_the_entries(name):
    records, _ = read_store(FIX / name)
    final = fold_records(records)
    want = final.snapshot()["entries"]
    for k in range(len(records) + 1):
        held = fold_records(records[:k]).snapshot()
        assert rebuild(held["entries"], final.snapshot(held["rev"])) == want, k


def stream(name: str):
    """Replay an OpenCode fixture the way a session does (deltas through
    ``live``, the rest as records) and yield the fold after every step."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="deep", model="m", cwd="/x")
    own("send", text=first_prompt(name))
    for ln in lines(name):
        evs = f.parse("opencode", ln)
        if json.loads(ln)["type"] == DELTA:
            f.live(evs)
        else:
            f.apply({"i": i, "ts": 1.0 + i, "src": "opencode", "line": ln}, evs)
            i += 1
        yield f


@pytest.mark.parametrize("name", ["plain", "tool", "edit", "task", "abort"])
def test_a_delta_between_two_moments_of_a_stream_rebuilds_the_later_one(name):
    """A client that left mid-stream and returns while the part still streams:
    a live entry grows without a store record, so only the rule that every
    delta carries the live entries brings its new text."""
    seen: list[tuple[list[dict], int]] = []
    for f in stream(name):
        now = f.snapshot()
        copy = json.loads(json.dumps(now["entries"]))
        for held, rev in seen[-40:]:
            assert rebuild(held, f.snapshot(rev)) == copy
        seen.append((copy, now["rev"]))


def test_a_since_past_the_store_gets_a_full_snapshot():
    r = Rec()
    r.own("send", text="hello")
    r.echo("hello")
    f, _ = run(r)
    for since in (5, 99, -2):
        snap = f.snapshot(since)
        assert "since" not in snap and snap["entries"] == f.snapshot()["entries"]


def test_an_empty_fold_has_rev_minus_one_and_a_delta_from_it_is_everything():
    f = Fold()
    assert f.snapshot() == {"rev": -1, "entries": []}
    r = Rec()
    r.own("send", text="hello")
    g, _ = run(r)
    assert rebuild([], g.snapshot(-1)) == g.snapshot()["entries"]


def test_entry_finds_one_entry_by_id():
    r = Rec()
    r.call("t1", "Bash", {"command": "ls"})
    f, _ = run(r)
    assert f.entry("t1")["kind"] == "tool"
    assert f.entry("nope") is None


def test_wire_drops_what_a_closed_row_shows_and_says_there_is_more():
    r = Rec()
    r.call(
        "t1",
        "Edit",
        {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"},
    )
    r.output("t1", "The file a.py has been updated.")
    r.call("t2", "Bash", {"command": "ls"})
    r.output("t2", "a\nb")
    f, _ = run(r)
    for e in f.entries():
        w = wire(e)
        assert w["detail"]["more"] is True
        assert not set(LAZY["tool"]) & set(w["detail"])
        assert w["detail"]["result"] == e["detail"]["result"]
        assert w["summary"] == e["summary"] and w["rev"] == e["rev"]
        assert "args" in e["detail"], "the fold itself keeps everything"


def test_wire_leaves_prose_errors_and_files_whole():
    for e in (
        {"id": "p", "kind": "prose", "md": "hi", "detail": {}},
        {"id": "x", "kind": "error", "md": None, "detail": {"tail": "stderr"}},
        {"id": "f", "kind": "file", "md": None, "detail": {"url": "/files/a/b"}},
    ):
        assert wire(e) is e


def test_a_recap_is_not_lazy_on_the_wire():
    """A recap's detail is what its row shows, open or folded: nothing to fetch."""
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.text("done it")
    r.result()
    r.own(
        "recap",
        upto=3,
        context="fixing it",
        ask="merge?",
        model="m",
        cost_usd=0.004,
        duration_ms=1800,
    )
    f, _ = run(r)
    (e,) = [x for x in f.snapshot()["entries"] if x["kind"] == "recap"]
    assert wire(e) is e and "more" not in e["detail"]
    assert {"context", "ask", "model", "cost_usd", "folded"} <= set(e["detail"])
    (folded,) = [
        op["upsert"]
        for op in wire_ops(f.apply(r.own("send", text="next")))
        if op.get("upsert", {}).get("kind") == "recap"
    ]
    assert folded["detail"] == {**e["detail"], "folded": True}


def test_wire_drops_thinking_text_but_not_an_empty_thought():
    e = {"id": "t", "kind": "thinking", "md": "deep thoughts", "detail": {}}
    assert wire(e)["md"] is None and wire(e)["detail"]["more"] is True
    empty = {"id": "u", "kind": "thinking", "md": "", "detail": {}}
    assert wire(empty) is empty


def test_wire_ops_project_upserts_and_pass_removals():
    e = {"id": "t", "kind": "thinking", "md": "deep", "detail": {}}
    assert wire_ops([{"upsert": e}, {"remove": "z"}]) == [
        {"upsert": wire(e)},
        {"remove": "z"},
    ]


def test_snapshots_carry_projected_entries():
    r = Rec()
    r.call("t1", "Bash", {"command": "ls"})
    r.output("t1", "a\nb")
    f, _ = run(r)
    (e,) = f.snapshot()["entries"]
    assert "tail" not in e["detail"] and e["detail"]["more"] is True
    (d,) = f.snapshot(-1)["entries"]
    assert "tail" not in d["detail"]


def test_an_artifacts_records_rebuild_from_every_cut_and_events_are_lazy():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.own(
        "artifact",
        artifact_id="art-aaaa0009",
        file_id="F",
        name="index.html",
        title="T",
        caption="c",
        state={"n": 0},
        started=True,
    )
    for n in range(3):
        r.own("artifact_state", artifact_id="art-aaaa0009", state={"n": n}, by="page")
    r.own("artifact_event", artifact_id="art-aaaa0009", name="hover", data=1)
    r.own("artifact_submit", artifact_id="art-aaaa0009", data={"p": 1}, label="done")
    r.text("thanks")
    r.result()
    final = fold_records(r.records)
    want = final.snapshot()["entries"]
    for k in range(len(r.records) + 1):
        held = fold_records(r.records[:k]).snapshot()
        assert rebuild(held["entries"], final.snapshot(held["rev"])) == want, k
    art = next(e for e in want if e["kind"] == "artifact")
    assert "events" not in art["detail"] and art["detail"]["more"] is True
    assert art["detail"]["state"] == {"n": 2}  # state rides the wire
