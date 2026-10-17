import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_tokens_count.py"
spec = importlib.util.spec_from_file_location("bench_tokens_count", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

FIX = Path(__file__).resolve().parent / "fixtures" / "tokbench"


def test_a_split_request_counts_once_with_all_its_tools():
    reqs = c.read_requests(FIX / "run1")
    r = [q for q in reqs if "mcp__aegis__turn_end" in q.tools]
    assert len(r) == 1 and len(reqs) == 6


def test_a_cut_off_line_is_skipped_not_fatal():
    assert len(c.read_requests(FIX / "run1")) == 6


def test_cost_reads_the_previous_context_and_writes_the_rest():
    # one transcript: ctx 20k then 25k, outputs 100 and 200
    reqs = [c.Request("t", "1", 20_000, 100, ()), c.Request("t", "2", 25_000, 200, ())]
    want = (20_000 * 6.25 + 100 * 25 + 20_000 * 0.5 + 5_000 * 6.25 + 200 * 25) / 1e6
    assert c.neutral_cost(reqs) == pytest.approx(want)


def test_each_transcript_starts_cold():
    reqs = [c.Request("t1", "1", 10_000, 0, ()), c.Request("t2", "2", 10_000, 0, ())]
    assert c.neutral_cost(reqs) == pytest.approx(2 * 10_000 * 6.25 / 1e6)


def test_summary_splits_aegis_bookkeeping_from_aegis_work():
    s = c.summarize(c.read_requests(FIX / "run1"))
    assert s["transcripts"] == 3 and s["bookkeeping_requests"] == 1
    assert s["first_ctx"] == 20_000


def run(cost, passed=True):
    return {
        "cost": cost,
        "passed": passed,
        "requests": 10,
        "bookkeeping_cost": cost / 10,
        "wall": 60,
    }


def test_headline_is_the_geometric_mean_over_tasks_passing_in_both():
    cmp = c.compare(
        {"a": [run(1.0)], "b": [run(1.0)]}, {"a": [run(1.21)], "b": [run(1.0)]}, None
    )
    assert cmp.headline == pytest.approx(1.1)


def test_a_task_failing_in_either_arm_is_listed_and_left_out():
    cmp = c.compare(
        {"a": [run(1.0)], "b": [run(1.0)]},
        {"a": [run(2.0)], "b": [run(9.0, passed=False)]},
        None,
    )
    assert cmp.failed == ["b"] and cmp.headline == pytest.approx(2.0)


def test_warns_when_the_headline_is_five_points_worse_than_last_release():
    cmp = c.compare(
        {"a": [run(1.0)]}, {"a": [run(1.20)]}, {"headline": 1.15, "a": 1.15}
    )
    assert cmp.warnings == ["::warning title=aegis token bench::headline 1.15 → 1.20"]


def test_a_task_that_moved_more_than_25_percent_is_marked_for_a_rerun():
    cmp = c.compare({"a": [run(1.0)]}, {"a": [run(1.5)]}, {"headline": 1.1, "a": 1.1})
    assert cmp.rerun == ["a"]


def test_the_table_has_a_row_per_task_and_the_headline_row():
    out = c.table(c.compare({"a": [run(1.0)]}, {"a": [run(1.1)]}, None))
    assert "| a |" in out and "geomean (1)" in out
