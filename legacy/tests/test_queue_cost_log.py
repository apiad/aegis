import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.events import Result, TokenUsage
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent

from tests.test_queue_manager import StubSessionManager, AssistantText


@pytest.mark.asyncio
async def test_completed_record_includes_cost(tmp_path):
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus")},
        sm, inbox, state_dir=tmp_path,
        handle_factory=lambda used: "w1",
    )
    # TokenUsage fields: input, cache_creation, cache_read, output
    # true_input = input + cache_creation + cache_read = 10_000 + 0 + 0
    usage = TokenUsage(input=10_000, output=5_000, cache_read=0,
                       cache_creation=0)
    sm.script("w1",
              [AssistantText(text="DONE"),
               Result(duration_ms=1, is_error=False, usage=usage)])
    tid, _ = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.1)

    log = tmp_path / "queues" / "impl.jsonl"
    assert log.exists()
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    done = [r for r in records if r.get("event") in ("completed", "failed")]
    assert len(done) == 1
    assert done[0]["event"] == "completed"
    assert "cost" in done[0]
    c = done[0]["cost"]
    # opus 4.7 rates (Anthropic docs + models.dev):
    #   in=5/M, out=25/M, cache_hit=0.50/M
    # c_in = true_input = 10_000 + 0 + 0 = 10_000
    # c_out = output = 5_000
    # cost = 10_000*5/1M + 5_000*25/1M = 0.05 + 0.125 = 0.175
    assert Decimal(c["usd"]) == Decimal("0.175")
    assert c["input_tokens"] == 10_000
    assert c["output_tokens"] == 5_000


@pytest.mark.asyncio
async def test_unknown_model_records_error_instead_of_crashing(tmp_path):
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="x", max_parallel=1,
                       provider="madeup", model="zzz")},
        sm, inbox, state_dir=tmp_path,
        handle_factory=lambda used: "w1",
    )
    usage = TokenUsage(input=1, output=1, cache_read=0, cache_creation=0)
    sm.script("w1",
              [AssistantText(text="DONE"),
               Result(duration_ms=1, is_error=False, usage=usage)])
    qm.enqueue("impl", "x", enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.1)

    log = tmp_path / "queues" / "impl.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    done = [r for r in records if r.get("event") in ("completed", "failed")]
    assert len(done) == 1
    assert done[0]["cost"].get("error") == "unknown_model"


@pytest.mark.asyncio
async def test_a_parked_worker_record_carries_cost(tmp_path):
    """Was `test_a_bad_turn_end_writes_no_cost_record`, the tripwire for the
    gap Task 7 left: a worker that stalled its budget away burned tokens
    nobody billed.

    Closed on `recoverable`, which is written once per task — not on
    `stalled`, which is written once per attempt against cumulative session
    metrics and would make a summing consumer count the same turn several
    times.
    """
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       max_attempts=1)},
        sm, inbox, state_dir=tmp_path,
        handle_factory=lambda used: "w1",
    )
    usage = TokenUsage(input=1_000, output=500, cache_read=0,
                       cache_creation=0)
    sm.script("w1",
              [AssistantText(text="oops"),
               Result(duration_ms=1, is_error=True, usage=usage)])
    tid, _ = qm.enqueue("impl", "x", enqueued_by=sender_agent("p"),
                        callback=False)
    await asyncio.sleep(0.1)

    log = tmp_path / "queues" / "impl.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert not [r for r in records if r.get("event") in ("completed", "failed")]
    parked = [r for r in records if r.get("event") == "recoverable"]
    assert len(parked) == 1 and parked[0]["task_id"] == tid
    # opus 4.7: in=5/M, out=25/M. 1_000*5/1M + 500*25/1M = 0.005 + 0.0125
    assert Decimal(parked[0]["cost"]["usd"]) == Decimal("0.0175")
    assert not [r for r in records
                if r.get("event") == "stalled" and "cost" in r]
