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
