"""The schedule definitions and the run log, folded and compacted."""

import pytest
from pydantic import ValidationError

from aegis.schedule_log import (
    KEEP_FIRES,
    Definition,
    RunLog,
    Schedule,
    compact,
    fold,
    rows,
)

FIRE = {"target": "new", "agent": "opus"}


def rec(t, ev, s="pr-triage", **kw):
    return {"t": f"2026-10-14T{t}", "s": s, "ev": ev, **kw}


def tick(t, out, tk, s="pr-triage", **kw):
    return rec(t, "tick", s, tick=tk, out=out, **kw)


def test_the_spec_example_folds_to_its_state():
    st = fold(
        [
            rec("09:00:00", "created", tick=1000),
            tick("09:30:00", "quiet", 1300, rc=0, sha="9f2c"),
            tick("09:35:00", "fired", 1600, rc=0, sha="1ab0"),
            rec("09:35:01", "fired", run=12, log_id="L12", stdout="#241"),
            tick("09:40:00", "held", 1900, why="overlap"),
        ]
    )["pr-triage"]
    assert (st.last_tick, st.sha, st.runs) == (1900, "1ab0", 12)
    assert (st.held, st.held_tick) == ("overlap", 1900)
    assert list(st.live) == [12] and st.live[12].log_id == "L12"


def test_errors_count_in_a_row_and_a_success_or_resume_resets_them():
    st = fold([tick("08:00:00", "error", 1, rc=4)] * 2)["pr-triage"]
    assert st.errors == 2
    st = fold([tick("08:00:00", "error", 1, rc=4), tick("08:10:00", "quiet", 2, rc=1)])[
        "pr-triage"
    ]
    assert st.errors == 0
    st = fold([tick("08:00:00", "error", 1, rc=4), rec("08:05:00", "resumed", tick=5)])[
        "pr-triage"
    ]
    assert st.errors == 0 and st.last_tick == 5


def test_a_finished_run_is_no_longer_live():
    st = fold(
        [
            rec("09:35:01", "fired", run=1, log_id="L1"),
            rec("09:39:11", "finished", run=1, attention="done"),
        ]
    )["pr-triage"]
    assert st.live == {} and st.runs == 1


def test_compaction_merges_quiet_stretches_and_drops_deleted_schedules():
    recs = [tick(f"09:{m:02d}:00", "quiet", m) for m in range(5)]
    recs.append(tick("09:10:00", "quiet", 10, s="gone"))
    out = compact(recs, {"pr-triage"})
    assert len(out) == 1
    assert (out[0]["n"], out[0]["from"], out[0]["to"], out[0]["tick"]) == (
        5,
        "2026-10-14T09:00:00",
        "2026-10-14T09:04:00",
        4,
    )
    assert fold(out)["pr-triage"].last_tick == 4


def test_compaction_keeps_the_last_100_fires_and_what_follows_them():
    recs = [rec("09:00:00", "created", tick=0)]
    for k in range(1, 131):
        recs.append(rec("09:00:00", "fired", run=k, log_id=f"L{k}"))
        recs.append(rec("09:00:01", "finished", run=k, attention="done"))
    out = compact(recs, {"pr-triage"})
    fired = [r["run"] for r in out if r["ev"] == "fired"]
    assert len(fired) == KEEP_FIRES and fired[0] == 31
    assert fold(out)["pr-triage"].runs == 130


def test_rows_join_a_fire_with_its_end_and_leave_out_its_tick():
    rs = [
        tick("09:30:00", "quiet", 1),
        tick("09:31:00", "quiet", 2),
        tick("09:35:00", "fired", 3, sha="x"),
        rec("09:35:01", "fired", run=12, log_id="L12", stdout="#241"),
        rec(
            "09:39:11",
            "finished",
            run=12,
            attention="needs_you",
            closed=False,
            secs=250,
            cost_usd=0.21,
        ),
        tick("09:40:00", "error", 4, rc=4, err="gh: HTTP 502"),
    ]
    out = rows(rs, lambda log_id: {"L12": "pr-triage-run"}.get(log_id))
    assert [r["kind"] for r in out] == ["quiet", "fired", "error"]
    assert out[0]["n"] == 2
    assert out[1] | {} == {
        "t": "2026-10-14T09:35:01",
        "kind": "fired",
        "run": 12,
        "log_id": "L12",
        "handle": "pr-triage-run",
        "stdout": "#241",
        "live": False,
        "attention": "needs_you",
        "closed": False,
        "secs": 250,
        "cost_usd": 0.21,
    }
    assert out[2]["err"] == "gh: HTTP 502"


@pytest.mark.parametrize(
    "raw,says",
    [
        ({"clock": {"every": "0m"}, "fire": FIRE, "prompt": "x"}, "smallest interval"),
        ({"clock": {"cron": "0 0 31 2 *"}, "fire": FIRE, "prompt": "x"}, "no tick"),
        (
            {"clock": {"every": "5m"}, "fire": {"target": "existing"}, "prompt": "x"},
            "names its session",
        ),
        (
            {
                "clock": {"every": "5m"},
                "overlap": "parallel",
                "fire": {"target": "existing", "session": "a-b"},
                "prompt": "x",
            },
            "parallel needs a new session",
        ),
        (
            {"clock": {"every": "5m"}, "fire": FIRE, "prompt": ""},
            "at least 1 character",
        ),
        (
            {
                "clock": {"every": "5m"},
                "check": {"bash": "true", "timeout_s": 301},
                "fire": FIRE,
                "prompt": "x",
            },
            "less than or equal to 300",
        ),
    ],
)
def test_a_bad_definition_says_what_is_wrong(raw, says):
    with pytest.raises(ValidationError) as e:
        Definition.model_validate(raw)
    assert says in str(e.value)


def test_a_schedule_round_trips_through_its_stored_form():
    d = Definition.model_validate(
        {
            "clock": {"every": "5m"},
            "check": {"bash": "true"},
            "fire": FIRE,
            "prompt": "go",
        }
    )
    s = Schedule("pr-triage", {"kind": "user", "principal": "ana"}, 10, 20, d)
    back = Schedule.load("pr-triage", s.dump())
    assert back == s and back.d.check.timeout_s == 30


def test_the_log_skips_a_damaged_line(tmp_path):
    log = RunLog(tmp_path / "schedule-runs.jsonl")
    log.append(rec("09:00:00", "created", tick=1))
    with log.path.open("a") as f:
        f.write("{not json\n")
    log.append(rec("09:00:00", "created", s="other", tick=2))
    assert [r["s"] for r in log.read()] == ["pr-triage", "other"]
    assert len(log.of("other")) == 1
    log.rewrite(log.of("other"))
    assert [r["s"] for r in log.read()] == ["other"]
